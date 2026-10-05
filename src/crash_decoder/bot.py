import asyncio
import io
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import discord
import httpx
from discord import app_commands

from .bundle import DEFAULT_BUNDLE, load
from .decoder import Decoder
from .jobs import BusyError, JobQueue
from .layout import parse_layout
from .models import Result
from .report import diagnostic, discord_summary

log = logging.getLogger(__name__)
HEALTH = Path("/tmp/crash-decoder-health") if os.name != "nt" else Path(".cache/health")


def collect_images(message: discord.Message) -> list[tuple[str, str]]:
    images = []
    seen = set()
    for attachment in message.attachments:
        if (attachment.content_type or "").startswith("image/") or Path(
            attachment.filename
        ).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            images.append((attachment.filename, attachment.proxy_url or attachment.url))
    for index, embed in enumerate(message.embeds, 1):
        for image in (embed.image, embed.thumbnail):
            if image and image.url:
                images.append((f"Embed {index}", image.proxy_url or image.url))
        if embed.type == "image" and embed.url:
            images.append((f"Embed {index}", embed.url))
    unique = []
    for label, url in images:
        parts = urlsplit(url)
        # Signed CDN query strings do not distinguish copies of an attachment.
        key = (parts.hostname, parts.path)
        if key not in seen:
            seen.add(key)
            unique.append((label, url))
    return unique


def payload(results: list[Result], *, attach_diagnostics: bool = False) -> dict:
    summary, full = discord_summary(results)
    files = []
    if attach_diagnostics:
        files.append(
            discord.File(io.BytesIO(diagnostic(results).encode()), filename="crash-diagnostic.json")
        )
    if full:
        files.insert(0, discord.File(io.BytesIO(full.encode()), filename="crash-trace.txt"))
    content = discord.utils.escape_markdown(summary)
    if len(content) > 2000:
        content = "Crash decode — full trace attached."
        if not full:
            files.insert(0, discord.File(io.BytesIO(summary.encode()), filename="crash-trace.txt"))
    return {"content": content, "files": files, "allowed_mentions": discord.AllowedMentions.none()}


class CorrectionModal(discord.ui.Modal, title="Correct OCR text"):
    def __init__(self, view):
        super().__init__(timeout=300)
        self.owner_view = view
        self.index = view.index
        crash = view.results[self.index].crash
        self.text = discord.ui.TextInput(
            label="Crash text",
            style=discord.TextStyle.paragraph,
            default=crash.text[:4000] if crash else "",
            max_length=4000,
        )
        self.add_item(self.text)
        layout = (
            {"sections": crash.runtime_sections, "allocations": crash.module_allocations}
            if crash
            else {}
        )
        self.layout = discord.ui.TextInput(
            label="Runtime REL layout JSON (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            default=json.dumps(layout) if any(layout.values()) else "",
            max_length=4000,
        )
        self.add_item(self.layout)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.user.id != self.owner_view.owner:
            await interaction.edit_original_response(
                content="Only the requester may correct this decode."
            )
            return
        await self.owner_view.rerun(
            interaction, corrected=str(self.text), index=self.index, layout=str(self.layout)
        )


class CorrectionView(discord.ui.View):
    def __init__(self, decoder, results, reply, owner, *, attach_diagnostics=False):
        super().__init__(timeout=600)
        self.decoder, self.results, self.reply, self.owner = decoder, results, reply, owner
        self.attach_diagnostics = attach_diagnostics
        self.index = 0
        self.selected = {}
        self.build_page = 0
        self.lock = asyncio.Lock()
        self.screenshot = discord.ui.Select(
            placeholder="Choose screenshot",
            options=[
                discord.SelectOption(label=f"Screenshot {i + 1}: {r.label}"[:100], value=str(i))
                for i, r in enumerate(results)
            ],
        )
        self.screenshot.callback = self.choose_screenshot
        self.add_item(self.screenshot)
        self.build = discord.ui.Select(
            placeholder="Choose game/build map",
            options=self.build_options(),
        )
        self.build.callback = self.choose_build
        self.add_item(self.build)

    def build_options(self):
        builds = self.decoder.bundle["builds"][self.build_page * 25 : (self.build_page + 1) * 25]
        return [
            discord.SelectOption(label=f"{b['game']} / {b['id']}"[:100], value=b["id"])
            for b in builds
        ]

    @discord.ui.button(label="Next build page", row=3)
    async def next_build_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        pages = (len(self.decoder.bundle["builds"]) + 24) // 25
        self.build_page = (self.build_page + 1) % pages
        self.build.options = self.build_options()
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner:
            await interaction.response.send_message(
                "Only the requester may correct this decode.", ephemeral=True
            )
            return False
        return True

    async def choose_screenshot(self, interaction):
        self.index = int(self.screenshot.values[0])
        await interaction.response.edit_message(
            content=f"{self.reply.jump_url}\nEditing screenshot {self.index + 1}.", view=self
        )

    async def choose_build(self, interaction):
        selected, index = self.build.values[0], self.index
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.rerun(interaction, selected=selected, index=index)

    @discord.ui.button(label="Correct OCR", style=discord.ButtonStyle.secondary)
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CorrectionModal(self))

    async def rerun(self, interaction, selected=None, corrected=None, index=None, layout=None):
        index = self.index if index is None else index
        async with self.lock:
            old = self.results[index]
            selection = selected or self.selected.get(index)
            text = corrected if corrected is not None else old.crash.text if old.crash else ""
            original = old.crash.original_text if old.crash else ""
            try:
                sections, allocations = (
                    parse_layout(layout)
                    if layout is not None
                    else (
                        (old.crash.runtime_sections, old.crash.module_allocations)
                        if old.crash
                        else ({}, {})
                    )
                )
            except ValueError as exc:
                await interaction.edit_original_response(content=str(exc))
                return
            result = await asyncio.to_thread(
                self.decoder.text, text, old.label, selection, original, sections, allocations
            )
            if old.crash:
                result.crash.ocr_passes = old.crash.ocr_passes
            updated = list(self.results)
            updated[index] = result
            data = payload(updated, attach_diagnostics=self.attach_diagnostics)
            try:
                await self.reply.edit(
                    content=data["content"],
                    attachments=data["files"],
                    allowed_mentions=data["allowed_mentions"],
                )
            except discord.HTTPException:
                await interaction.edit_original_response(
                    content="Could not update the reply; it may have been deleted."
                )
                return
            self.results = updated
            if selection:
                self.selected[index] = selection
            await interaction.edit_original_response(
                content=f"Updated screenshot {index + 1}: {self.reply.jump_url}"
            )


class CrashBot(discord.Client):
    def __init__(self, bundle, *, workers=2, capacity=8, attach_diagnostics=False):
        super().__init__(
            intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none()
        )
        self.decoder = Decoder(bundle)
        self.attach_diagnostics = attach_diagnostics
        self.jobs = JobQueue(self.decoder, workers, capacity)
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(20), trust_env=False)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(
            app_commands.ContextMenu(name="Decode crash", callback=self.decode_crash)
        )
        self.health_task = None

    async def setup_hook(self):
        self.jobs.start()
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.health_task = asyncio.create_task(self.health_loop())

    async def health_loop(self):
        HEALTH.parent.mkdir(parents=True, exist_ok=True)
        while True:
            if (
                self.is_ready()
                and not self.is_closed()
                and all(not t.done() for t in self.jobs.tasks)
            ):
                HEALTH.write_text(str(time.time()))
            await asyncio.sleep(15)

    async def close(self):
        if self.health_task:
            self.health_task.cancel()
            await asyncio.gather(self.health_task, return_exceptions=True)
        HEALTH.unlink(missing_ok=True)
        await self.jobs.close()
        await self.http_client.aclose()
        await super().close()

    async def decode_crash(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer(ephemeral=True, thinking=True)
        images = collect_images(message)
        if not images:
            await interaction.edit_original_response(
                content="This message has no image attachments or image embeds."
            )
            return
        if len(images) > 20:
            await interaction.edit_original_response(
                content="This message exceeds the 20-image limit; split it into smaller messages."
            )
            return
        pending = True

        async def progress(index, total):
            if pending:
                try:
                    await interaction.edit_original_response(
                        content=f"Decoding screenshot {index}/{total}…"
                    )
                except discord.HTTPException:
                    pass  # Expired progress messages do not discard successful OCR.

        try:
            await interaction.edit_original_response(
                content=f"Queued {len(images)} images for decoding…"
            )
            results = await asyncio.wait_for(
                self.jobs.submit(
                    interaction.user.id,
                    message.id,
                    lambda: self.jobs.images(images, self.http_client, progress),
                ),
                timeout=600,
            )
            reply = await message.reply(
                **payload(results, attach_diagnostics=self.attach_diagnostics), mention_author=False
            )
        except BusyError as exc:
            await interaction.edit_original_response(content=str(exc))
            return
        except TimeoutError:
            pending = False
            await interaction.edit_original_response(
                content="Decode timed out; try fewer screenshots."
            )
            return
        except discord.HTTPException:
            await interaction.edit_original_response(
                content="Could not reply. Check View Channel, Read Message History, "
                "Send Messages/Send Messages in Threads and Attach Files permissions; "
                "the selected message may have been deleted."
            )
            return
        except Exception:
            log.exception("Decode request failed")
            await interaction.edit_original_response(content="Decode failed; check the bot logs.")
            return
        view = CorrectionView(
            self.decoder,
            results,
            reply,
            interaction.user.id,
            attach_diagnostics=self.attach_diagnostics,
        )
        await interaction.edit_original_response(
            content=(
                f"Decoded: {reply.jump_url}\nUse the controls below to choose a map or edit OCR."
            ),
            view=view,
        )


def run():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("Set DISCORD_TOKEN at runtime")
    bundle = load(Path(os.getenv("SYMBOL_BUNDLE", str(DEFAULT_BUNDLE))))
    bot = CrashBot(
        bundle,
        workers=int(os.getenv("OCR_WORKERS", "2")),
        capacity=int(os.getenv("QUEUE_CAPACITY", "8")),
        attach_diagnostics=os.getenv("ATTACH_DIAGNOSTICS", "false").strip().lower()
        in {"1", "true", "yes", "on"},
    )
    bot.run(token, log_level=logging.INFO)
