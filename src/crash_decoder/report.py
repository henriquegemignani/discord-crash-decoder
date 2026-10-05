import json
from dataclasses import asdict

from .models import Result


def heading(result: Result, number: int) -> str:
    detection = result.detection
    build = detection.build_id if detection and detection.build_id else "unknown build"
    if detection and detection.status == "tentative":
        build = "build not selected (tentative)"
    return f"Screenshot {number}: {result.label} - {build}"


def render_screenshot(result: Result, number: int, *, include_heading: bool = True) -> str:
    lines = [heading(result, number)] if include_heading else []
    if result.error:
        lines.append(result.error)
    if result.crash and result.crash.exception:
        lines.append(result.crash.exception)
    for resolution in result.resolutions:
        if resolution.symbol:
            line = f"{resolution.symbol} +0x{resolution.offset:x}"
        else:
            frame = resolution.frame
            address = (
                f"{frame.module}+0x{frame.address:x}"
                if frame.kind == "module_offset"
                else f"0x{frame.address:08x}"
            )
            line = f"{address}: unresolved ({resolution.reason or 'no matching symbol'})"
        locations = []
        if resolution.binary_kind == "rel" and resolution.binary:
            module = resolution.binary
            locations.append(module if module.endswith(".rel") else f"{module}.rel")
        if resolution.source:
            locations.append(resolution.source)
        if locations:
            line += f" [{'; '.join(locations)}]"
        lines.append(line)
    return "\n".join(lines)


def render(results: list[Result]) -> str:
    return "\n\n".join(
        render_screenshot(result, number) for number, result in enumerate(results, 1)
    )


def diagnostic(results: list[Result]) -> str:
    return json.dumps([asdict(result) for result in results], indent=2)
