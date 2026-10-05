import re

from .models import Crash, Detection


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def detect(crash: Crash, builds: list[dict], selected: str | None = None) -> Detection:
    ids = [b["id"] for b in builds]
    if selected:
        if selected not in ids:
            raise ValueError(f"Unknown build: {selected}")
        return Detection(
            "identified", selected, [selected], ["User selected map; binary unverified."]
        )
    evidence = []
    candidates = builds
    explicit = [b for b in builds if re.search(rf"\b{re.escape(b['id'])}\b", crash.text, re.I)]
    if explicit:
        candidates = explicit
        evidence.append("Explicit build identifier")
    game = None
    if re.search(r"echoes|metroid prime 2|\bRFO\s*:", crash.text, re.I):
        game = "echoes"
    elif re.search(r"metroid prime(?:\s+1)?\b", crash.text, re.I):
        game = "prime"
    if game:
        candidates = [b for b in candidates if b["game"] == game]
        evidence.append(f"Game/format hint: {game}")
    if match := re.search(r"\b(?:revision|rev\.?)\s*(\d+)\b", crash.text, re.I):
        revision = f"{int(match.group(1)):02d}"
        candidates = [b for b in candidates if b["revision"] == revision]
        evidence.append(f"Explicit revision: {revision}")
    for pattern, field, value in (
        (r"\b(?:NTSC-U|USA|US)\b", "region", "US"),
        (r"\b(?:NTSC-J|Japan|Japanese)\b", "region", "JP"),
        (r"\b(?:PAL|Europe)\b", "region", "EU"),
        (r"\bWii\b", "platform", "Wii"),
        (r"\bGameCube\b", "platform", "GameCube"),
    ):
        if re.search(pattern, crash.text, re.I):
            candidates = [b for b in candidates if b[field] == value]
            evidence.append(f"Explicit {field}: {value}")
    if crash.build_string:
        matches = [
            b
            for b in candidates
            if normalize(crash.build_string) in {normalize(s) for s in b["build_strings"]}
        ]
        if not matches:
            return Detection(
                "unknown", None, [], [*evidence, "Unsupported/conflicting full build string"]
            )
        if len(matches) == 1:
            identified = matches[0]
            if crash.ip and crash.ip.address != 0:
                executable = identified["binaries"][0]
                in_section = any(
                    s["start"] <= crash.ip.address < s["end"]
                    for s in executable["sections"]
                    if s["executable"]
                )
                evidence.append(
                    "IP consistent with main executable sections"
                    if in_section
                    else "IP outside main sections; could be REL, corrupted or modified code"
                )
            return Detection(
                "identified",
                matches[0]["id"],
                [matches[0]["id"]],
                [*evidence, "Unique full build string; modified code remains unverified."],
            )
        candidates = matches
        evidence.append("Full build string shared by multiple builds")
    elif match := re.search(r"Build\s+v([\d.]+)", crash.text, re.I):
        prefix = f"build v{match.group(1)} "
        known = [
            b
            for b in candidates
            if any(normalize(s).startswith(prefix) for s in b["build_strings"])
        ]
        # Builds without known strings cannot be ruled out by a version alone.
        candidates = [b for b in candidates if b in known or not b["build_strings"]]
        evidence.append("Partial version only")
    if not candidates:
        return Detection("unknown", None, [], [*evidence, "Conflicting build evidence"])
    status = "tentative" if evidence else "unknown"
    return Detection(
        status,
        None,
        [b["id"] for b in candidates],
        evidence or ["Shared screen/address format cannot identify a binary."],
    )
