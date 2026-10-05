import re

from .models import Crash, Frame

# Eight digits only: malformed/truncated fields stay unparsed, never padded.
HEX = r"(?:[0oO]{1,2}[xX])([0-9a-fA-FoOiIlL]{8})(?![0-9A-Za-z])"
ADDRESS = re.compile(HEX)
BUILD = re.compile(r"Build\s+v[\d.]+\s+\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}", re.I)
REGISTER = re.compile(rf"\b(r\d{{1,2}}|LR|CR|SRR[01]|DSISR|DAR|Mem)\s*[=:]\s*{HEX}", re.I)


def parse(text: str, *, original_text: str | None = None) -> Crash:
    crash = Crash(original_text if original_text is not None else text, text)

    def address(match: re.Match, group: int = 1) -> int:
        raw = match.group(group)
        corrected = raw.translate(
            str.maketrans({"O": "0", "o": "0", "i": "1", "I": "1", "l": "1", "L": "1"})
        )
        prefix = re.search(r"[0oO]{1,2}[xX]", match.group(0)).group(0)
        if raw != corrected or prefix != "0x":
            crash.corrections.append(f"Address field: {match.group(0)} -> 0x{corrected}")
        return int(corrected, 16)

    if match := BUILD.search(text):
        crash.build_string = " ".join(match.group(0).split())
    for line in text.splitlines():
        if (
            re.search(r"exception|infinite loop|assert|alloc failed|object list full", line, re.I)
            and not crash.exception
        ):
            crash.exception = line.strip()
        if match := re.search(rf"\b(?:IP|SRR0)\s*[=:]\s*{HEX}", line, re.I):
            if crash.ip is None:
                crash.ip = Frame(address(match), "ip", match.group(0))
        for match in REGISTER.finditer(line):
            crash.registers[match.group(1).upper()] = address(match, 2)
        if match := re.match(rf"\s*RFO\s*:\s*{HEX}\s*:\s*(\S+)", line, re.I):
            crash.frames.append(
                Frame(address(match), "module_offset", line.strip(), module=match.group(2))
            )
            continue
        if re.match(rf"\s*{HEX}\s*:", line):
            fields = list(ADDRESS.finditer(line))
            if len(fields) == 3:
                crash.frames.append(
                    Frame(
                        address(fields[2]),
                        "return",
                        line.strip(),
                        address(fields[0]),
                        address(fields[1]),
                    )
                )
            else:
                crash.warnings.append(f"Unparsed stack row: {line.strip()}")
    if original_text is not None and text != original_text:
        crash.corrections.insert(0, "Text corrected; original OCR preserved.")
    if not crash.readable:
        crash.warnings.append("No complete instruction pointer or stack address found.")
    return crash
