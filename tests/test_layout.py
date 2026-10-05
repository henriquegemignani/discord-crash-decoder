import pytest

from crash_decoder.layout import parse_layout


def test_explicit_runtime_layout():
    sections, allocations = parse_layout(
        '{"sections":{"AIMannedTurret":{".text":"0x81000000"}},'
        '"allocations":{"AIMannedTurret":"0x80ffff00"}}'
    )
    assert sections["AIMannedTurret"][".text"] == 0x81000000
    assert allocations["AIMannedTurret"] == 0x80FFFF00


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        '{"sections":[]}',
        '{"sections":{"a":{".text":true}}}',
        '{"sections":{"a":{".text":-1}}}',
        '{"allocations":{"a":4294967296}}',
        '{"other":0}',
    ],
)
def test_invalid_layout(text):
    with pytest.raises(ValueError, match="Invalid REL"):
        parse_layout(text)
