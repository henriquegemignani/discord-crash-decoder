import json
import re
import subprocess
from pathlib import Path

import yaml

from .bundle import SCHEMA_VERSION, canonical, write

REPOSITORIES = {game: f"https://github.com/PrimeDecomp/{game}.git" for game in ("prime", "echoes")}
SYMBOL = re.compile(r"^(.*?) = (\S+):0x([0-9A-Fa-f]+);\s*//\s*(.*)$")
RANGE = re.compile(r"\s+(\S+)\s+start:0x([0-9A-Fa-f]+) end:0x([0-9A-Fa-f]+)")


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def checkout(game: str, revision: str, cache: Path) -> Path:
    if not revision or revision.startswith("-"):
        raise ValueError("Invalid upstream revision")
    destination = cache / game
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                REPOSITORIES[game],
                str(destination),
            ],
            check=True,
        )
    git("fetch", "--depth", "1", "origin", revision, cwd=destination)
    git("checkout", "--detach", "FETCH_HEAD", cwd=destination)
    return destination


def metadata_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Upstream metadata path escapes checkout")
    return path


def import_binary(root: Path, config: dict, kind: str) -> dict:
    if "symbols" not in config or "splits" not in config:
        return {
            "id": Path(config["object"]).stem,
            "kind": kind,
            "object": config["object"],
            "hash": config.get("hash"),
            "hash_algorithm": "sha1",
            "sections": [],
            "functions": [],
            "limitations": ["Upstream does not supply module symbol/split metadata."],
        }
    sections = {}
    ranges = []
    source = None
    for line in metadata_path(root, config["splits"]).read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            source = line[:-1]
        if source == "Sections" and "type:" in line:
            name = line.split()[0]
            sections[name] = {
                "name": name,
                "executable": "type:code" in line,
                "start": 2**32,
                "end": 0,
            }
        elif match := RANGE.match(line):
            section, start, end = match.groups()
            ranges.append((section, int(start, 16), int(end, 16), source))
    functions = []
    for line in metadata_path(root, config["symbols"]).read_text(encoding="utf-8").splitlines():
        match = SYMBOL.match(line)
        if not match:
            if "type:function" in line:
                raise ValueError(f"Unrecognized function metadata: {line}")
            continue
        name, section, address, attributes = match.groups()
        if "type:function" not in attributes:
            continue
        size_match = re.search(r"\bsize:0x([0-9A-Fa-f]+)", attributes)
        if not size_match:
            raise ValueError(f"Function size missing: {name}")
        address, size = int(address, 16), int(size_match.group(1), 16)
        if not size:
            continue
        sec = sections.get(section)
        if not sec or not sec["executable"]:
            raise ValueError(f"Function in unknown/non-code section: {name}")
        sec["start"] = min(sec["start"], address)
        sec["end"] = max(sec["end"], address + size)
        sources = {
            src for s, a, b, src in ranges if s == section and a <= address and address + size <= b
        }
        functions.append(
            {
                "name": name,
                "address": address,
                "size": size,
                "section": section,
                "source": next(iter(sources)) if len(sources) == 1 else None,
            }
        )
    return {
        "id": Path(config["object"]).name if kind == "dol" else Path(config["object"]).stem,
        "kind": kind,
        "object": config["object"],
        "hash": config.get("hash"),
        "hash_algorithm": "sha1" if config.get("hash") else None,
        "sections": sorted((s for s in sections.values() if s["end"]), key=lambda s: s["name"]),
        "functions": sorted(functions, key=lambda f: (f["section"], f["address"], f["name"])),
        "limitations": [
            "REL symbols use section-relative offsets; runtime section bases or "
            "verified REL file section offsets required."
        ]
        if kind == "rel"
        else (
            ["RSO module metadata is not imported; only the main executable is mapped."]
            if config.get("selfile")
            else []
        ),
    }


def import_checkout(game: str, root: Path, registry: dict) -> list[dict]:
    if git("status", "--porcelain", "--", "config", "src", "include", cwd=root):
        raise ValueError("Local decomp metadata must be committed for reproducible provenance")
    commit = git("rev-parse", "HEAD", cwd=root)
    builds = []
    for path in sorted((root / "config").glob("*/config.yml")):
        ident = path.parent.name
        if not re.fullmatch(r"[GR][A-Z0-9]{5}(?:_\d{2})?", ident):
            continue
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        info = registry.get(ident, {})
        strings = list(info.get("build_strings", []))
        if (build_file := path.parent / "build_string.txt").exists():
            strings.append(build_file.read_text(encoding="utf-8").strip())
        binaries = [import_binary(root, config, "dol")]
        binaries.extend(import_binary(root, module, "rel") for module in config.get("modules", []))
        builds.append(
            {
                "id": ident,
                "game": game,
                "platform": "GameCube" if ident.startswith("G") else "Wii",
                "region": {"E": "US", "J": "JP", "P": "EU"}.get(ident[3], "Other"),
                "revision": ident.split("_")[1] if "_" in ident else "00",
                "executable": config["object"],
                "build_strings": sorted(set(strings)),
                "upstream": REPOSITORIES[game],
                "upstream_commit": commit,
                "verification": {
                    "map_available": True,
                    "screenshot_parsing": info.get("screenshot_parsing", False),
                    "automatic_detection": info.get("automatic_detection", False),
                },
                "binaries": binaries,
            }
        )
    if not builds:
        raise ValueError(f"No supported configs in {root}")
    return builds


def update(
    output: Path,
    registry_path: Path,
    games: list[str],
    roots: dict[str, Path],
    revisions: dict[str, str],
    cache: Path,
) -> dict:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    builds = []
    if output.exists():
        from .bundle import load

        builds = [b for b in load(output)["builds"] if b["game"] not in games or b.get("custom")]
    upstreams = {}
    for game in games:
        root = roots.get(game) or checkout(game, revisions.get(game, "main"), cache)
        imported = import_checkout(game, root, registry)
        builds.extend(imported)
        upstreams[game] = imported[0]["upstream_commit"]
    bundle = {"schema_version": SCHEMA_VERSION, "builds": sorted(builds, key=lambda b: b["id"])}
    write(output, bundle)
    save_lock(output, bundle)
    return bundle


def save_lock(output: Path, bundle: dict):
    lock = {
        "schema_version": SCHEMA_VERSION,
        "bundle_checksum": bundle["checksum"],
        "upstreams": {
            b["game"]: b["upstream_commit"] for b in bundle["builds"] if not b.get("custom")
        },
        "custom_upstreams": {
            b["id"]: b["upstream_commit"] for b in bundle["builds"] if b.get("custom")
        },
    }
    output.with_suffix(".lock.json").write_bytes(canonical(lock) + b"\n")


def register(
    output: Path,
    root: Path,
    config: str,
    identity: str,
    game: str,
    platform: str,
    region: str,
    revision: str,
    strings: list[str],
) -> dict:
    from .bundle import load

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identity):
        raise ValueError("Custom build ID must use letters, digits, dot, underscore or hyphen")
    bundle = load(output)
    if any(b["id"] == identity for b in bundle["builds"]):
        raise ValueError(
            "Build ID already registered; use a separate identity for changed binaries"
        )
    path = metadata_path(root, config)
    if git("status", "--porcelain", cwd=root):
        raise ValueError("Custom decomp checkout must be committed for reproducible provenance")
    configuration = yaml.safe_load(path.read_text(encoding="utf-8"))
    binaries = [import_binary(root, configuration, "dol")]
    binaries.extend(
        import_binary(root, module, "rel") for module in configuration.get("modules", [])
    )
    bundle["builds"].append(
        {
            "id": identity,
            "game": game,
            "platform": platform,
            "region": region,
            "revision": revision,
            "executable": configuration["object"],
            "build_strings": sorted(set(strings)),
            "custom": True,
            "upstream": "local-decomp",
            "upstream_commit": git("rev-parse", "HEAD", cwd=root),
            "verification": {
                "map_available": True,
                "screenshot_parsing": False,
                "automatic_detection": False,
            },
            "binaries": binaries,
        }
    )
    bundle["builds"].sort(key=lambda b: b["id"])
    write(output, bundle)
    save_lock(output, bundle)
    return bundle


def diff(old: dict, new: dict) -> str:
    before, after = ({b["id"]: b for b in bundle["builds"]} for bundle in (old, new))
    lines = ["Symbol bundle update", ""]
    for ident in sorted(before.keys() | after.keys()):
        if ident not in before:
            lines.append(f"- New build: {ident}")
        elif ident not in after:
            lines.append(f"- COVERAGE REGRESSION: removed {ident}")
        else:

            def functions(build):
                return {
                    (b["id"], f["section"], f["address"]): f
                    for b in build["binaries"]
                    for f in b["functions"]
                }

            a, b = functions(before[ident]), functions(after[ident])
            changed = sum(a[k] != b[k] for k in a.keys() & b.keys())
            lost = sum(f["size"] for f in a.values()) - sum(f["size"] for f in b.values())
            lines.append(
                f"- {ident}: +{len(b.keys() - a.keys())} / -{len(a.keys() - b.keys())} "
                f"functions, {changed} changed"
            )
            if a.keys() - b.keys() or lost > 0:
                lines.append(f"  COVERAGE REGRESSION: lost symbols or {max(lost, 0)} code bytes")
    return "\n".join(lines) + "\n"
