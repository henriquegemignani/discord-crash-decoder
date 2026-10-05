from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Frame:
    address: int
    kind: Literal["ip", "return", "module_offset"]
    raw: str
    stack_pointer: int | None = None
    back_chain: int | None = None
    module: str | None = None


@dataclass
class Crash:
    original_text: str
    text: str
    build_string: str | None = None
    exception: str | None = None
    ip: Frame | None = None
    frames: list[Frame] = field(default_factory=list)
    registers: dict[str, int] = field(default_factory=dict)
    corrections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr_passes: list[dict[str, str]] = field(default_factory=list)
    runtime_sections: dict[str, dict[str, int]] = field(default_factory=dict)
    module_allocations: dict[str, int] = field(default_factory=dict)

    @property
    def readable(self) -> bool:
        return self.ip is not None or bool(self.frames)


@dataclass
class Detection:
    status: Literal["identified", "tentative", "unknown"]
    build_id: str | None
    candidates: list[str]
    evidence: list[str]


@dataclass
class Resolution:
    frame: Frame
    symbol: str | None = None
    original_symbol: str | None = None
    offset: int | None = None
    binary: str | None = None
    source: str | None = None
    reason: str | None = None


@dataclass
class Result:
    label: str
    crash: Crash | None = None
    detection: Detection | None = None
    resolutions: list[Resolution] = field(default_factory=list)
    error: str | None = None
    bundle_checksum: str = ""
