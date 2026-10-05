import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import httpx
import pytest

from crash_decoder.bot import (
    CorrectionModal,
    CorrectionView,
    CrashBot,
    collect_images,
    payload,
    run,
)
from crash_decoder.jobs import BusyError, JobQueue
from crash_decoder.models import Result


def interaction(user=1):
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        response=SimpleNamespace(
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
    )


def attachment(name="a.png", url="https://cdn.discordapp.com/attachments/1/2/a.png?ex=1"):
    return SimpleNamespace(content_type="image/png", filename=name, url=url, proxy_url=url)


def test_collect_images_and_deduplicate():
    embed = discord.Embed.from_dict(
        {"image": {"url": "https://external.example/a.png", "proxy_url": attachment().url}}
    )
    message = SimpleNamespace(
        attachments=[attachment(), attachment(url=attachment().url + "&hm=x")],
        embeds=[
            embed,
            discord.Embed.from_dict(
                {"thumbnail": {"url": "https://media.discordapp.net/attachments/1/3/b.png"}}
            ),
        ],
    )
    images = collect_images(message)
    assert len(images) == 2
    assert all("external.example" not in url for _, url in images)


@pytest.mark.parametrize("channel_type", [discord.TextChannel, discord.Thread])
@pytest.mark.parametrize("attach_diagnostics", [False, True])
@pytest.mark.parametrize(
    "header",
    [
        "Build v1.088 10/29/2002 2:21:25",
        "Exception 2 - Debug\nBuild v9.999 1/1/2099 1:00:00",
    ],
)
async def test_one_actual_reply_and_private_link(
    bundle, decoder, channel_type, header, attach_diagnostics
):
    bot = CrashBot(bundle, attach_diagnostics=attach_diagnostics)
    command = bot.tree.get_command("Decode crash", type=discord.AppCommandType.message)
    assert command is not None and not bot.intents.message_content
    request = interaction()
    reply = SimpleNamespace(jump_url="https://discord.com/channels/1/2/3", edit=AsyncMock())
    state = SimpleNamespace(
        allowed_mentions=discord.AllowedMentions.none(),
        http=SimpleNamespace(send_message=AsyncMock(return_value={"id": "3"})),
        create_message=lambda **kwargs: reply,
    )
    channel = object.__new__(channel_type)
    channel.id, channel.guild, channel._state = 2, SimpleNamespace(id=1), state
    selected = object.__new__(discord.Message)
    selected.id, selected.channel, selected.guild, selected._state = (
        101,
        channel,
        channel.guild,
        state,
    )
    selected.attachments, selected.embeds = [attachment()], []
    result = decoder.text(f"{header}\nIP: 0x802d68c8")

    async def submit(*args):
        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        return [result, Result("bad", error="Unreadable image")]

    bot.jobs.submit = AsyncMock(side_effect=submit)
    try:
        await bot.decode_crash(request, selected)
        state.http.send_message.assert_awaited_once()
        params = state.http.send_message.call_args.kwargs["params"]
        arguments = json.loads(params.multipart[0]["value"]) if params.multipart else params.payload
        assert arguments["allowed_mentions"] == {"parse": [], "replied_user": False}
        assert arguments["message_reference"] == {"message_id": 101, "channel_id": 2, "guild_id": 1}
        assert "Unreadable image" in arguments["content"]
        filenames = {attachment["filename"] for attachment in arguments.get("attachments", [])}
        assert ("crash-diagnostic.json" in filenames) is attach_diagnostics
        response = request.edit_original_response.call_args.kwargs
        assert reply.jump_url in response["content"]
        assert response["view"].owner == request.user.id
        assert response["view"].attach_diagnostics is attach_diagnostics
    finally:
        await bot.close()


async def test_private_error_when_cannot_reply(bundle, decoder):
    bot = CrashBot(bundle)
    request = interaction()
    selected = SimpleNamespace(id=101, attachments=[attachment()], embeds=[], reply=AsyncMock())
    selected.reply.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        {"message": "Missing Permissions", "code": 50013},
    )
    bot.jobs.submit = AsyncMock(return_value=[decoder.text("IP: 0x80003100")])
    try:
        await bot.decode_crash(request, selected)
        assert "Could not reply" in request.edit_original_response.call_args.kwargs["content"]
    finally:
        await bot.close()


@pytest.mark.parametrize("attach_diagnostics", [False, True])
def test_long_report_attached(decoder, attach_diagnostics):
    results = [decoder.text("IP: 0x80003100", "X" * 150) for _ in range(20)]
    data = payload(results, attach_diagnostics=attach_diagnostics)
    assert len(data["content"]) <= 2000
    expected = {"crash-trace.txt"}
    if attach_diagnostics:
        expected.add("crash-diagnostic.json")
    assert {f.filename for f in data["files"]} == expected


def test_diagnostics_preserve_debug_unknown_and_failed_results(decoder):
    text = "Exception 2 - Debug\nBuild v9.999 1/1/2099 1:00:00\nIP: 0x802d68c8"
    results = [
        decoder.text(text, "debug.png"),
        decoder.text("IP: 0x80003100", "unknown.png"),
        Result("failed.png", error="Unreadable image", bundle_checksum=decoder.bundle["checksum"]),
    ]
    data = payload(results, attach_diagnostics=True)
    try:
        attachment = next(f for f in data["files"] if f.filename == "crash-diagnostic.json")
        diagnostics = json.loads(attachment.fp.read())
        assert diagnostics[0]["crash"]["original_text"] == text
        assert diagnostics[0]["crash"]["ip"]["address"] == 0x802D68C8
        assert diagnostics[0]["detection"]["status"] == "unknown"
        assert diagnostics[0]["resolutions"][0]["symbol"] is None
        assert diagnostics[1]["detection"]["build_id"] is None
        assert diagnostics[2]["error"] == "Unreadable image"
        assert all(d["bundle_checksum"] == decoder.bundle["checksum"] for d in diagnostics)
    finally:
        for attachment in data["files"]:
            attachment.close()


@pytest.mark.parametrize("attach_diagnostics", [False, True])
async def test_corrections_use_existing_text_preserve_original_and_modal_index(
    decoder, attach_diagnostics
):
    initial = "IP: 0x80003100"
    results = [decoder.text(initial, "first"), decoder.text("IP: 0x80003140", "second")]
    reply = SimpleNamespace(jump_url="https://discord.com/channels/1/2/3", edit=AsyncMock())
    view = CorrectionView(decoder, results, reply, owner=1, attach_diagnostics=attach_diagnostics)
    modal = CorrectionModal(view)
    view.index = 1
    assert modal.index == 0
    request = interaction()
    await view.rerun(request, selected="GM8E01_00", corrected="IP: 0x80003140", index=modal.index)
    assert view.results[0].crash.original_text == initial
    assert view.results[0].detection.build_id == "GM8E01_00"
    assert view.results[1] is results[1]
    assert view.results[0].resolutions[0].offset == 0
    filenames = {attachment.filename for attachment in reply.edit.call_args.kwargs["attachments"]}
    assert ("crash-diagnostic.json" in filenames) is attach_diagnostics
    denied = interaction(user=2)
    assert not await view.interaction_check(denied)
    denied.response.send_message.assert_awaited_once()


@pytest.mark.parametrize(
    ("setting", "expected"), [(None, False), ("false", False), ("true", True), ("1", True)]
)
def test_runtime_diagnostic_setting(monkeypatch, setting, expected):
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    if setting is None:
        monkeypatch.delenv("ATTACH_DIAGNOSTICS", raising=False)
    else:
        monkeypatch.setenv("ATTACH_DIAGNOSTICS", setting)
    bundle = {"builds": []}
    monkeypatch.setattr("crash_decoder.bot.load", lambda path: bundle)
    constructor = Mock(return_value=SimpleNamespace(run=Mock()))
    monkeypatch.setattr("crash_decoder.bot.CrashBot", constructor)
    run()
    assert constructor.call_args.kwargs["attach_diagnostics"] is expected
    assert constructor.call_args.args == (bundle,)
    constructor.return_value.run.assert_called_once()


async def test_private_selector_pages_custom_maps(decoder):
    altered = copy.copy(decoder)
    altered.bundle = {"builds": [{"id": f"custom-{i}", "game": "prime"} for i in range(51)]}
    view = CorrectionView(
        altered, [decoder.text("IP: 0x80003100")], SimpleNamespace(jump_url="link"), owner=1
    )
    assert len(view.build.options) == 25
    await view.next_build_page.callback(interaction())
    assert view.build.options[0].value == "custom-25"
    await view.next_build_page.callback(interaction())
    assert view.build.options[0].value == "custom-50"


async def test_bounded_queue_cooldown_duplicate_and_concurrency(decoder):
    queue = JobQueue(decoder, workers=1, capacity=1, cooldown=20)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def work():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "done"

    queue.start()
    first = asyncio.create_task(queue.submit(1, 101, work))
    await entered.wait()
    with pytest.raises(BusyError, match="already"):
        await queue.submit(2, 101, work)
    with pytest.raises(BusyError, match="wait"):
        await queue.submit(1, 102, work)
    second = asyncio.create_task(queue.submit(2, 102, work))
    await asyncio.sleep(0)
    with pytest.raises(BusyError, match="full"):
        await queue.submit(3, 103, work)
    assert calls == 1
    release.set()
    assert await first == await second == "done"
    with pytest.raises(BusyError, match="already"):
        await queue.submit(4, 101, work)
    await queue.close()


async def test_bad_image_isolated_and_content_deduplicated(decoder, monkeypatch):
    queue = JobQueue(decoder)

    async def fake_download(client, url):
        if url == "bad":
            raise ValueError("Unreadable image")
        return b"identical bytes"

    def fake_image(data, label):
        return decoder.text("IP: 0x80003100", label)

    monkeypatch.setattr("crash_decoder.jobs.download", fake_download)
    monkeypatch.setattr(decoder, "image", fake_image)
    async with httpx.AsyncClient() as client:
        results = await queue.images([("a", "one"), ("bad", "bad"), ("dup", "two")], client)
    assert len(results) == 2 and results[0].crash is not None and results[1].error
