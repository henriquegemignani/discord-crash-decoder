import copy
import json
from pathlib import Path

import pytest

from crash_decoder.bundle import checksum, validate, write
from crash_decoder.detection import detect
from crash_decoder.models import Frame
from crash_decoder.parser import parse
from crash_decoder.resolver import Resolver, demangle


def test_addresses_corrected_only_in_fields():
    crash = parse(
        "INFINITE LOOP\nIP: Ox8O2d68c8 Mem: 0xd1dd0d1e\n"
        "0x805bf088: 0x805bf3d8 0x8O2d68e0\nr0=0x00000000 r31=0xffffffff"
    )
    assert crash.exception == "INFINITE LOOP"
    assert crash.ip.address == 0x802D68C8
    assert crash.frames[0].address == 0x802D68E0
    assert crash.registers == {"MEM": 0xD1DD0D1E, "R0": 0, "R31": 0xFFFFFFFF}
    assert len(crash.corrections) == 2
    assert "LOOP" in crash.text


@pytest.mark.parametrize("field", ["0x1234567", "0x123456789", "0x1234567Z", "0x8000 1234"])
def test_no_invented_digits(field):
    assert parse(f"IP: {field}").ip is None


def test_original_text_preserved():
    crash = parse("IP: 0x80003100", original_text="IP: OOPS")
    assert crash.original_text == "IP: OOPS"
    assert crash.corrections


def test_bounds_and_gaps(bundle):
    build = next(b for b in bundle["builds"] if b["id"] == "GM8E01_00")
    resolver = Resolver(build)
    function = build["binaries"][0]["functions"][0]
    start = function["address"]
    assert resolver.resolve(Frame(start, "ip", "")).offset == 0
    assert (
        resolver.resolve(Frame(start + function["size"] - 4, "return", "")).offset
        == function["size"] - 4
    )
    assert resolver.resolve(Frame(start + 1, "return", "")).symbol is None
    assert resolver.resolve(Frame(0, "ip", "")).symbol is None
    assert resolver.resolve(Frame(0xFFFFEAB3, "return", "")).symbol is None
    synthetic = copy.deepcopy(build)
    synthetic["binaries"][0]["functions"] = [function]
    assert Resolver(synthetic).resolve(Frame(start + function["size"], "return", "")).symbol is None


def test_demangle():
    assert demangle("BuildLight__9CGuiLightCFv") == "CGuiLight::BuildLight() const"
    assert demangle("main") == "main"


def test_detection_conservative(bundle):
    builds = bundle["builds"]
    assert detect(parse("IP: 0x802d68c8"), builds).status == "unknown"
    partial = detect(parse("Build v1.111\nIP: 0x802d68c8"), builds)
    assert partial.status == "tentative" and partial.build_id is None
    known = parse("Build v1.088 10/29/2002 2:21:25\nIP: 0x802d68c8")
    assert detect(known, builds).build_id == "GM8E01_00"
    assert detect(parse("Echoes\n" + known.text), builds).status == "unknown"
    assert detect(parse("Build v9.999 1/1/2099 1:00:00"), builds).status == "unknown"
    assert detect(known, builds, "G2ME01").build_id == "G2ME01"
    twin = copy.deepcopy(builds[0])
    twin["id"] = "CUSTOM"
    twin["build_strings"] = [known.build_string]
    assert detect(known, [*builds, twin]).status == "tentative"


def test_module_safety(bundle):
    build = next(b for b in bundle["builds"] if b["id"] == "G2ME01")
    resolver = Resolver(build)
    frame = parse("RFO:0x00000084: AIMannedTurret.rel").frames[0]
    assert resolver.resolve(frame).symbol is None
    fn = next(
        f
        for b in build["binaries"]
        if b["id"] == "AIMannedTurret"
        for f in b["functions"]
        if f["name"] == "RELMain"
    )
    runtime = {"AIMannedTurret": {".text": 0x81000000}}
    address = Frame(0x81000000 + fn["address"], "return", "", module="AIMannedTurret")
    assert resolver.resolve(address).symbol is None
    assert resolver.resolve(address, runtime).symbol == "RELMain"
    anonymous = Frame(address.address, "ip", "")
    assert resolver.resolve(anonymous, runtime).symbol == "RELMain"
    offset = Frame(0x100 + fn["address"], "module_offset", "", module="AIMannedTurret.rel")
    assert (
        resolver.resolve(offset, runtime, module_allocations={"AIMannedTurret": 0x80FFFF00}).symbol
        == "RELMain"
    )


def test_bundle_tamper_and_determinism(bundle, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write(first, copy.deepcopy(bundle))
    write(second, copy.deepcopy(bundle))
    assert first.read_bytes() == second.read_bytes()
    bad = copy.deepcopy(bundle)
    bad["builds"][0]["id"] = "tampered"
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(bundle)
    bad["builds"][0]["binaries"][0]["functions"][0]["address"] = 3.5
    bad["checksum"] = checksum(bad)
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(bundle)
    bad["builds"][0]["binaries"][0]["functions"][0]["size"] = 0
    bad["checksum"] = checksum(bad)
    with pytest.raises(ValueError):
        validate(bad)


def test_corpus_expected_text(decoder):
    for sample in json.loads(Path("tests/corpus/expected.json").read_text()):
        # Manually labeled addresses validate symbol resolution independently of OCR.
        build = decoder.builds[sample["build"]]
        text = build["build_strings"][0] + f"\nIP: 0x{sample['ip']}\n"
        for address in sample["returns"]:
            text += f"0x805bf000: 0x805bf100 0x{address}\n"
        result = decoder.text(text)
        assert result.detection.build_id == sample["build"]
        assert result.resolutions[0].frame.address == int(sample["ip"], 16)
        assert len(result.resolutions) == len(sample["returns"]) + 1
        assert result.resolutions[0].original_symbol == sample["ip_symbol"]
        assert result.resolutions[0].offset == sample["ip_offset"]
