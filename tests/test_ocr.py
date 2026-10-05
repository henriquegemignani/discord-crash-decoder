import json
import shutil
from pathlib import Path

import pytest

CORPUS = Path(__file__).parent / "corpus"
SAMPLES = json.loads((CORPUS / "expected.json").read_text())


@pytest.mark.skipif(
    not shutil.which("tesseract"), reason="Native Tesseract required (runs in CI/container)"
)
@pytest.mark.parametrize("sample", SAMPLES, ids=[s["file"] for s in SAMPLES])
def test_real_screenshot(decoder, sample):
    result = decoder.image((CORPUS / sample["file"]).read_bytes(), sample["file"])
    assert result.detection.build_id == sample["build"], result.crash.text
    assert result.crash.ip.address == int(sample["ip"], 16), result.crash.text
    assert result.resolutions[0].original_symbol == sample["ip_symbol"]
    assert result.resolutions[0].offset == sample["ip_offset"]
    assert [f.address for f in result.crash.frames] == [int(a, 16) for a in sample["returns"]], (
        result.crash.text
    )


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Native Tesseract required")
def test_cropped_screen_never_selects_by_address_matches(decoder):
    import io

    from PIL import Image

    with Image.open(CORPUS / "prime-allocation.png") as image:
        cropped = image.crop((0, 550, image.width, image.height))
        data = io.BytesIO()
        cropped.save(data, format="PNG")
    result = decoder.image(data.getvalue(), "cropped stack")
    assert result.crash.frames
    assert result.detection.status == "unknown" and result.detection.build_id is None
    assert all(r.symbol is None for r in result.resolutions)
