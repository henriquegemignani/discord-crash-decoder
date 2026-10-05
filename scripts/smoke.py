"""Container acceptance checks without credentials or development dependencies."""

import json
import os
import time
from pathlib import Path

from crash_decoder.bundle import load
from crash_decoder.decoder import Decoder


def main():
    if hasattr(os, "getuid"):
        assert os.getuid() != 0, "Runtime must be non-root"
    root = Path(__file__).resolve().parents[1]
    corpus = root / "tests" / "corpus"
    decoder = Decoder(load())
    samples = json.loads((corpus / "expected.json").read_text())
    for sample in samples:
        start = time.monotonic()
        result = decoder.image((corpus / sample["file"]).read_bytes(), sample["file"])
        assert result.detection.build_id == sample["build"], result.crash.text
        assert result.crash.ip.address == int(sample["ip"], 16), result.crash.text
        actual = [f.address for f in result.crash.frames]
        expected = [int(a, 16) for a in sample["returns"]]
        assert actual == expected, (sample["file"], actual, expected, result.crash.text)
        print(
            f"PASS {sample['file']} {result.detection.build_id} {len(actual)} frames "
            f"{time.monotonic() - start:.2f}s",
            flush=True,
        )
    print(f"Validated bundle {decoder.bundle['checksum']}", flush=True)


if __name__ == "__main__":
    main()
