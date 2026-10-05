import json

from crash_decoder.models import Crash, Detection, Frame, Resolution, Result
from crash_decoder.report import diagnostic, render, render_screenshot


def test_compact_trace_matches_requested_format():
    result = Result(
        "image.png",
        crash=Crash(
            "original OCR",
            "corrected OCR",
            exception="Alloc failed — Size: -419500209 -— Produc",
            corrections=["Address field corrected"],
            warnings=["OCR warning"],
        ),
        detection=Detection("identified", "GM8E01_00", ["GM8E01_00"], ["Full build evidence"]),
        resolutions=[
            Resolution(
                Frame(0x802D68C8, "ip", "IP: 0x802d68c8"),
                symbol="rs_debugger_printf(char const *, ...)",
                original_symbol="rs_debugger_printf__FPCce",
                offset=0x9C,
                binary="main.dol",
                source="Kyoto/Basics/RAssertDolphin.cpp",
            ),
            Resolution(
                Frame(0x80008558, "return", "saved LR"),
                symbol="main",
                offset=0x54,
                binary="main.dol",
                source="MetroidPrime/main.cpp",
            ),
        ],
        bundle_checksum="test-checksum",
    )
    expected = (
        "Screenshot 1: image.png - GM8E01_00\n"
        "Alloc failed — Size: -419500209 -— Produc\n"
        "rs_debugger_printf(char const *, ...) +0x9c [main.dol; Kyoto/Basics/RAssertDolphin.cpp]\n"
        "main +0x54 [main.dol; MetroidPrime/main.cpp]"
    )
    assert render_screenshot(result, 1) == expected
    assert render([result, result]) == expected + "\n\n" + expected.replace(
        "Screenshot 1", "Screenshot 2"
    )
    details = json.loads(diagnostic([result]))[0]
    assert details["crash"]["original_text"] == "original OCR"
    assert details["crash"]["corrections"] == ["Address field corrected"]
    assert details["crash"]["warnings"] == ["OCR warning"]
    assert details["detection"]["evidence"] == ["Full build evidence"]
    assert details["resolutions"][0]["frame"]["address"] == 0x802D68C8
    assert details["bundle_checksum"] == "test-checksum"


def test_unresolved_addresses_and_module_offsets_remain_visible():
    result = Result(
        "unknown.png",
        crash=Crash("", ""),
        detection=Detection("tentative", None, ["GM8E01_00"], []),
        resolutions=[
            Resolution(Frame(0x806BF338, "return", ""), reason="Module layout unavailable"),
            Resolution(
                Frame(0x84, "module_offset", "", module="AIMannedTurret.rel"),
                reason="Module layout unavailable",
            ),
        ],
    )
    assert render_screenshot(result, 2) == (
        "Screenshot 2: unknown.png - build not selected (tentative)\n"
        "0x806bf338: unresolved (Module layout unavailable)\n"
        "AIMannedTurret.rel+0x84: unresolved (Module layout unavailable)"
    )


def test_failed_image_has_its_own_trace():
    result = Result("bad.png", error="Unreadable image")
    assert render_screenshot(result, 3) == "Screenshot 3: bad.png - unknown build\nUnreadable image"
