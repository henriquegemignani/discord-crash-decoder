import copy
import json
import subprocess
from pathlib import Path

import pytest

from crash_decoder.bundle import load
from crash_decoder.symbol_builder import diff, metadata_path, register, update


def commit(root: Path):
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Decoder test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Fixture metadata",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def decomp(tmp_path):
    root = tmp_path / "decomp"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
    config = root / "config" / "GM8E01_00"
    config.mkdir(parents=True)
    (config / "config.yml").write_text(
        "object: sys/main.dol\nhash: 949c5ed7368aef547e0b0db1c3678f466e2afbff\n"
        "symbols: config/GM8E01_00/symbols.txt\nsplits: config/GM8E01_00/splits.txt\n"
        "modules:\n- object: files/Unknown.rel\n",
        encoding="utf-8",
    )
    (config / "symbols.txt").write_text(
        "main = .text:0x80003100; // type:function size:0x10\n"
        "data = .data:0x80400000; // type:object size:0x4\n",
        encoding="utf-8",
    )
    (config / "splits.txt").write_text(
        "Sections:\n\t.text type:code align:4\n\t.data type:data align:4\n"
        "Game/main.cpp:\n\t.text start:0x80003100 end:0x80003200\n",
        encoding="utf-8",
    )
    (config / "build_string.txt").write_text("Build v1.088 10/29/2002 2:21:25\n", encoding="utf-8")
    commit(root)
    return root


def test_import_update_and_rollback_cycle(decomp, tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text("{}")
    output = tmp_path / "symbols.json"

    def generate():
        return update(output, registry, ["prime"], {"prime": decomp}, {}, tmp_path / "cache")

    before = generate()
    baseline = output.read_bytes()
    assert before["builds"][0]["binaries"][0]["functions"][0]["source"] == "Game/main.cpp"
    assert before["builds"][0]["binaries"][1]["limitations"]
    generate()
    assert baseline == output.read_bytes()
    symbols = decomp / "config" / "GM8E01_00" / "symbols.txt"
    symbols.write_text(symbols.read_text().replace("size:0x10", "size:0x20"))
    with pytest.raises(ValueError, match="committed"):
        generate()
    commit(decomp)
    after = generate()
    assert before["checksum"] != after["checksum"]
    assert "1 changed" in diff(before, after)
    assert after["builds"][0]["upstream_commit"] != before["builds"][0]["upstream_commit"]
    assert (
        json.loads(output.with_suffix(".lock.json").read_text())["bundle_checksum"]
        == after["checksum"]
    )
    output.write_bytes(baseline)
    assert load(output)["checksum"] == before["checksum"]


def test_regression_summary(bundle):
    after = copy.deepcopy(bundle)
    after["builds"][0]["binaries"][0]["functions"].pop()
    assert "COVERAGE REGRESSION" in diff(bundle, after)
    after["builds"].pop()
    assert "removed" in diff(bundle, after)


def test_metadata_path_cannot_escape(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        metadata_path(tmp_path, "../outside.txt")


def test_custom_maps_separate_and_preserved(decomp, tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text("{}")
    output = tmp_path / "symbols.json"
    update(output, registry, ["prime"], {"prime": decomp}, {}, tmp_path / "cache")
    register(
        output,
        decomp,
        "config/GM8E01_00/config.yml",
        "prime-mod-1",
        "prime",
        "GameCube",
        "US",
        "mod-1",
        ["Build v1.088 10/29/2002 2:21:25"],
    )
    custom = next(b for b in load(output)["builds"] if b.get("custom"))
    assert custom["id"] == "prime-mod-1" and not custom["verification"]["automatic_detection"]
    update(output, registry, ["prime"], {"prime": decomp}, {}, tmp_path / "cache")
    assert len(load(output)["builds"]) == 2
    lock = json.loads(output.with_suffix(".lock.json").read_text())
    assert lock["custom_upstreams"]["prime-mod-1"] == custom["upstream_commit"]
