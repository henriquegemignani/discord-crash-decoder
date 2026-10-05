"""Exercise metadata update, image upgrade and image rollback without a Discord token.

Uses explicitly synthetic, committed decomp fixtures in an isolated temporary context.
The production bundle in the workspace is never changed.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def command(args, *, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stderr[-5000:] + "\n" + result.stdout[-2000:] + "\n")
        result.check_returncode()
    return result.stdout.strip()


def main():
    project = Path(__file__).resolve().parents[1]
    cache = (project / ".cache").resolve()
    assert cache.is_relative_to(project)
    cache.mkdir(exist_ok=True)
    suffix = uuid.uuid4().hex[:8]
    baseline_tag = f"discord-crash-decoder:cycle-baseline-{suffix}"
    updated_tag = f"discord-crash-decoder:cycle-updated-{suffix}"
    with tempfile.TemporaryDirectory(prefix="cycle-", dir=cache) as temporary:
        work = Path(temporary).resolve()
        assert work.is_relative_to(cache)  # TemporaryDirectory cleanup stays inside task cache.
        context = work / "context"
        context.mkdir()
        for filename in ("Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock"):
            shutil.copy2(project / filename, context / filename)
        shutil.copytree(
            project / "src",
            context / "src",
            ignore=shutil.ignore_patterns("__pycache__", "symbols.json", "symbols.lock.json"),
        )
        registry = work / "registry.json"
        registry.write_text("{}")
        decomp = work / "decomp"
        config = decomp / "config" / "GM8E01_00"
        config.mkdir(parents=True)
        command(["git", "init", str(decomp)])
        command(["git", "config", "core.autocrlf", "false"], cwd=decomp)
        (config / "config.yml").write_text(
            "object: sys/main.dol\nsymbols: config/GM8E01_00/symbols.txt\n"
            "splits: config/GM8E01_00/splits.txt\n"
        )
        (config / "splits.txt").write_text(
            "Sections:\n\t.text type:code align:4\nSynthetic/cycle.cpp:\n"
            "\t.text start:0x80003100 end:0x80003200\n"
        )
        (config / "build_string.txt").write_text("Build v0.001 1/1/2000 0:00:00\n")
        symbols = config / "symbols.txt"
        output = context / "src" / "crash_decoder" / "data" / "symbols.json"
        code = context / "src" / "crash_decoder" / "__init__.py"
        original_code = code.read_text()

        def update(name, size):
            symbols.write_text(f"{name} = .text:0x80003100; // type:function size:0x{size:x}\n")
            command(["git", "add", "."], cwd=decomp)
            command(
                [
                    "git",
                    "-c",
                    "user.name=Decoder cycle test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-m",
                    name,
                ],
                cwd=decomp,
            )
            command(
                [
                    sys.executable,
                    "-m",
                    "crash_decoder.cli",
                    "symbols",
                    "update",
                    "--game",
                    "prime",
                    "--prime-checkout",
                    str(decomp),
                    "--registry",
                    str(registry),
                    "--output",
                    str(output),
                ],
                cwd=project,
            )
            command(
                [sys.executable, "-m", "crash_decoder.cli", "symbols", "validate", str(output)],
                cwd=project,
            )
            return json.loads(output.read_text())

        initial = update("SyntheticBaseline", 0x10)
        code.write_text(original_code + "\nCYCLE_VERSION = 'baseline'\n")
        initial_bytes = output.read_bytes()
        command(
            [
                sys.executable,
                "-m",
                "crash_decoder.cli",
                "symbols",
                "update",
                "--game",
                "prime",
                "--prime-checkout",
                str(decomp),
                "--registry",
                str(registry),
                "--output",
                str(output),
            ],
            cwd=project,
        )
        assert output.read_bytes() == initial_bytes
        command(["docker", "build", "-t", baseline_tag, str(context)])
        updated = update("SyntheticUpdated", 0x20)
        code.write_text(original_code + "\nCYCLE_VERSION = 'updated'\n")
        assert updated["checksum"] != initial["checksum"]
        command(["docker", "build", "-t", updated_tag, str(context)])
        text = work / "crash.txt"
        text.write_text("Build v0.001 1/1/2000 0:00:00\nIP: 0x80003110\n")

        def decode(tag):
            value = command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:size=256m",
                    "--mount",
                    f"type=bind,source={text},target=/input/crash.txt,readonly",
                    tag,
                    "decode",
                    "--text",
                    "--json",
                    "/input/crash.txt",
                ]
            )
            return json.loads(value)[0]

        old, new, rollback = decode(baseline_tag), decode(updated_tag), decode(baseline_tag)
        assert old["resolutions"][0]["symbol"] is None  # Exactly the old function's end.
        assert new["resolutions"][0]["symbol"] == "SyntheticUpdated"
        assert new["resolutions"][0]["offset"] == 0x10
        assert rollback == old
        assert old["bundle_checksum"] == initial["checksum"]
        assert new["bundle_checksum"] == updated["checksum"]

        def code_version(tag):
            return command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "python",
                    tag,
                    "-c",
                    "import crash_decoder; print(crash_decoder.CYCLE_VERSION)",
                ]
            )

        assert code_version(baseline_tag) == "baseline"
        assert code_version(updated_tag) == "updated"
        assert code_version(baseline_tag) == "baseline"
        report = {
            "synthetic_fixture": True,
            "baseline_image": baseline_tag,
            "updated_image": updated_tag,
            "baseline_checksum": initial["checksum"],
            "updated_checksum": updated["checksum"],
            "rollback_restored_baseline": True,
            "rollback_restored_code": True,
            "reproducible_bytes_sha256": hashlib.sha256(initial_bytes).hexdigest(),
        }
        (cache / "cycle-verification.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
    # Images are left available for inspection; only isolated temporary files are cleaned.


if __name__ == "__main__":
    main()
