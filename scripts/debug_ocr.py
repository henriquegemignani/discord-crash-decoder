"""Print OCR variants for a local screenshot when investigating regressions."""

import sys
from pathlib import Path

import pytesseract
from PIL import ImageOps

from crash_decoder.images import read_image
from crash_decoder.parser import parse

image = read_image(Path(sys.argv[1]).read_bytes())
image.thumbnail((2400, 2400))
gray = ImageOps.autocontrast(ImageOps.grayscale(image))
if gray.width < 1200:
    gray = gray.resize((gray.width * 2, gray.height * 2))
for index, candidate in enumerate(
    (gray, gray.point(lambda p: 255 if p > 150 else 0), ImageOps.invert(gray))
):
    for constrained in (False, True):
        config = "--psm 6"
        if constrained:
            config += ' -c tessedit_char_whitelist="0123456789abcdefABCDEFxX:IPMemRFO./_- "'
        text = pytesseract.image_to_string(candidate, config=config, timeout=12)
        parsed = parse(text)
        print(f"PASS {index} constrained={constrained} frames={len(parsed.frames)}\n{text}")
