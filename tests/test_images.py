import io

import httpx
import pytest
from PIL import Image

from crash_decoder.images import MAX_BYTES, allowed_url, download, read_image


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.discordapp.com/a",
        "https://cdn.discordapp.com.evil/a",
        "https://127.0.0.1/a",
        "https://cdn.discordapp.com:8443/a",
        "https://user:pass@cdn.discordapp.com/a",
        "file:///tmp/a",
    ],
)
def test_destinations_restricted(url):
    assert not allowed_url(url)


async def test_download_redirect_limit_and_status():
    url = "https://cdn.discordapp.com/attachments/1/2/a.png"
    for response in (
        httpx.Response(302, headers={"location": "http://127.0.0.1"}),
        httpx.Response(200, headers={"content-length": str(MAX_BYTES + 1)}),
        httpx.Response(200, content=b"x" * (MAX_BYTES + 1)),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, r=response: r)
        ) as client:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await download(client, url)


def test_pixels_and_format(monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (20, 20)).save(image, format="PNG")
    assert read_image(image.getvalue()).size == (20, 20)
    monkeypatch.setattr("crash_decoder.images.MAX_PIXELS", 300)
    with pytest.raises(ValueError, match="pixel"):
        read_image(image.getvalue())
    with pytest.raises(ValueError, match="Unreadable"):
        read_image(b"not an image")


def test_optional_ocr_timeout_keeps_valid_pass(monkeypatch):
    from crash_decoder.images import recognize

    data = io.BytesIO()
    Image.new("RGB", (20, 20)).save(data, format="PNG")
    responses = iter(["IP: 0x80003100", RuntimeError("timeout"), RuntimeError("timeout")])

    def ocr(*args, **kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("crash_decoder.images.pytesseract.image_to_string", ocr)
    monkeypatch.setattr(
        "crash_decoder.images.pytesseract.image_to_data",
        lambda *args, **kwargs: {"text": [], "top": []},
    )
    assert "0x80003100" in recognize(data.getvalue()).text
