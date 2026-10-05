import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_BUNDLE = Path(__file__).parent / "data" / "symbols.json"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def checksum(bundle: dict) -> str:
    return hashlib.sha256(
        canonical({k: v for k, v in bundle.items() if k != "checksum"})
    ).hexdigest()


def validate(bundle: dict) -> None:
    try:
        _validate(bundle)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("Invalid symbol bundle structure") from exc


def _validate(bundle: dict) -> None:
    if type(bundle.get("schema_version")) is not int:
        raise ValueError("Invalid schema version")
    if bundle.get("schema_version") != SCHEMA_VERSION or bundle.get("checksum") != checksum(bundle):
        raise ValueError("Unsupported or corrupt symbol bundle")
    builds = bundle["builds"]
    if not builds or len({b["id"] for b in builds}) != len(builds):
        raise ValueError("Empty bundle or duplicate build identity")
    for build in builds:
        for key in ("game", "platform", "region", "revision", "executable", "upstream_commit"):
            if not isinstance(build.get(key), str) or not build[key]:
                raise ValueError(f"Missing {key}: {build['id']}")
        if any(
            type(build["verification"][key]) is not bool
            for key in ("map_available", "screenshot_parsing", "automatic_detection")
        ):
            raise ValueError("Invalid build verification flags")
        if not isinstance(build["build_strings"], list) or any(
            not isinstance(s, str) for s in build["build_strings"]
        ):
            raise ValueError("Invalid known build strings")
        if not re.fullmatch(r"[0-9a-f]{40}", build["upstream_commit"]):
            raise ValueError("Upstream commit must be a full SHA")
        if not build["binaries"] or not build["binaries"][0]["functions"]:
            raise ValueError("Missing executable symbols")
        if build["binaries"][0]["kind"] != "dol" or len(
            {b["id"] for b in build["binaries"]}
        ) != len(build["binaries"]):
            raise ValueError("Missing main executable or duplicate module identity")
        for binary in build["binaries"]:
            if binary["kind"] not in {"dol", "rel"}:
                raise ValueError("Unsupported binary kind")
            if binary.get("hash") and not re.fullmatch(r"[0-9a-f]{40}", binary["hash"]):
                raise ValueError("Invalid binary SHA1")
            previous = {}
            sections = {s["name"]: s for s in binary["sections"]}
            if len(sections) != len(binary["sections"]):
                raise ValueError("Duplicate executable section identity")
            for section in sections.values():
                if (
                    type(section["start"]) is not int
                    or type(section["end"]) is not int
                    or type(section["executable"]) is not bool
                    or not 0 <= section["start"] < section["end"] <= 2**32
                ):
                    raise ValueError("Invalid executable section bounds")
                if section.get("file_offset") is not None and (
                    type(section["file_offset"]) is not int
                    or not 0 <= section["file_offset"] < 2**32
                ):
                    raise ValueError("Invalid REL file section offset")
            for function in binary["functions"]:
                if not isinstance(function["name"], str) or not function["name"]:
                    raise ValueError("Invalid function name")
                if function.get("source") is not None and not isinstance(function["source"], str):
                    raise ValueError("Invalid function source association")
                start, size, section = (function[k] for k in ("address", "size", "section"))
                if (
                    type(start) is not int
                    or type(size) is not int
                    or size <= 0
                    or start < 0
                    or start + size > 2**32
                ):
                    raise ValueError("Invalid symbol bounds")
                if section not in sections or not sections[section]["executable"]:
                    raise ValueError("Function outside an executable section")
                if start < previous.get(section, 0):
                    raise ValueError(f"Overlapping functions: {build['id']} {binary['id']}")
                previous[section] = start + size
                if (
                    not sections[section]["start"]
                    <= start
                    < start + size
                    <= sections[section]["end"]
                ):
                    raise ValueError("Symbol exceeds section bounds")


def load(path: Path | str = DEFAULT_BUNDLE) -> dict:
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(bundle)
    return bundle


def write(path: Path, bundle: dict) -> None:
    bundle["checksum"] = checksum(bundle)
    validate(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(canonical(bundle) + b"\n")
    temporary.replace(path)
