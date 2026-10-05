import asyncio
import io
import time
import warnings
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

from .parser import parse

MAX_BYTES = 10_000_000
MAX_PIXELS = 16_000_000
HOSTS = frozenset(
    {
        "cdn.discordapp.com",
        "media.discordapp.net",
        "images-ext-1.discordapp.net",
        "images-ext-2.discordapp.net",
    }
)


def allowed_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname in HOSTS
            and parts.port in (None, 443)
            and parts.username is None
            and parts.password is None
        )
    except ValueError:
        return False


async def download(client: httpx.AsyncClient, url: str) -> bytes:
    if not allowed_url(url):
        raise ValueError("Image URL must be hosted or proxied by Discord")
    async with asyncio.timeout(30):
        async with client.stream("GET", url, follow_redirects=False) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("Image redirects are disabled")
            length = response.headers.get("content-length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("Image exceeds download limit")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("Image exceeds download limit")
    return bytes(data)


def read_image(data: bytes) -> Image.Image:
    if len(data) > MAX_BYTES:
        raise ValueError("Image exceeds file size limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("Unsupported image format")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Image exceeds pixel limit")
                image.load()
                return ImageOps.exif_transpose(image).convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Unreadable or unsafe image") from exc


@dataclass
class OCRText:
    text: str
    original_text: str
    passes: list[dict[str, str]]


def recognize(data: bytes, timeout: float = 40) -> OCRText:
    image = read_image(data)
    # Bound preprocessing memory as well as input pixels.
    image.thumbnail((2400, 2400))
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    if gray.width < 1200 and gray.width * gray.height * 4 <= MAX_PIXELS:
        gray = gray.resize((gray.width * 2, gray.height * 2))
    passes = [gray, gray.point(lambda p: 255 if p > 150 else 0), ImageOps.invert(gray)]
    deadline = time.monotonic() + timeout
    results = []
    for index, candidate in enumerate(passes):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            text = pytesseract.image_to_string(
                candidate, lang="eng", config="--psm 6", timeout=min(remaining, 10)
            )
        except RuntimeError:
            continue
        results.append({"pass": f"ordinary-{index}", "text": text})
    if not results:
        raise ValueError("OCR timed out")

    def score(item):
        crash = parse(item["text"])
        return int(crash.build_string is not None), int(crash.ip is not None), len(crash.frames)

    ordinary = max(results, key=score)["text"]
    # Recognize only the address region with hexadecimal constraints. REL names
    # require ordinary OCR, so RFO screens keep the ordinary region candidates.
    if "RFO:" not in ordinary and deadline > time.monotonic():
        try:
            rows = pytesseract.image_to_data(
                gray,
                lang="eng",
                config="--psm 6",
                output_type=pytesseract.Output.DICT,
                timeout=min(5, deadline - time.monotonic()),
            )
            tops = [
                rows["top"][i]
                for i, word in enumerate(rows["text"])
                if word.strip().upper().startswith("IP:")
            ]
            if tops:
                for index, candidate in enumerate(passes):
                    if deadline <= time.monotonic():
                        break
                    region = candidate.crop((0, max(0, min(tops) - 5), gray.width, gray.height))
                    text = pytesseract.image_to_string(
                        region,
                        lang="eng",
                        config=(
                            "--psm 6 -c tessedit_char_whitelist="
                            '"0123456789abcdefABCDEFxX:IPMem./_- "'
                        ),
                        timeout=min(5, deadline - time.monotonic()),
                    )
                    results.append({"pass": f"hex-region-{index}", "text": text})
        except RuntimeError:
            pass  # A successful ordinary pass survives optional OCR timeout.

    # Choose complete fields from recorded passes, never add/remove hex digits.
    # Header and address regions can succeed under different preprocessing.
    header = ordinary.split("IP:")[0] if "IP:" in ordinary else ""
    addresses = max(
        results, key=lambda r: (len(parse(r["text"]).frames), int(parse(r["text"]).ip is not None))
    )["text"]
    address_lines = addresses.splitlines()
    ip_source = max(results, key=lambda r: int(parse(r["text"]).ip is not None))["text"]
    ip_line = next((line for line in ip_source.splitlines() if parse(line).ip), None)
    body = [
        line
        for line in address_lines
        if parse(line).frames
        or parse(line).warnings
        and "Unparsed stack row" in " ".join(parse(line).warnings)
    ]
    if ip_line is not None and body:
        best = header + ip_line + "\n" + "\n".join(body)
    else:
        best = ordinary
    return OCRText(best, ordinary, results)
