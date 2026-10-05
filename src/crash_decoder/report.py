import json
from dataclasses import asdict

from .models import Result


def render(results: list[Result]) -> str:
    lines = ["Crash decode", "Saved return addresses are preserved without call-site subtraction."]
    for number, result in enumerate(results, 1):
        lines.extend(["", f"Screenshot {number}: {result.label}"])
        if result.error:
            lines.append(result.error)
        if not result.crash:
            continue
        detection = result.detection
        lines.append(f"Build: {detection.build_id or 'not selected'} ({detection.status})")
        lines.extend(detection.evidence)
        if result.crash.exception:
            lines.append(result.crash.exception)
        for resolution in result.resolutions:
            frame = resolution.frame
            label = "IP" if frame.kind == "ip" else "RFO" if frame.module else "Return"
            symbol = (
                f"{resolution.symbol} +0x{resolution.offset:x}"
                if resolution.symbol
                else f"unresolved ({resolution.reason})"
            )
            suffix = f" [{resolution.binary}" if resolution.binary else ""
            if suffix:
                suffix += f"; {resolution.source}]" if resolution.source else "]"
            lines.append(f"{label} 0x{frame.address:08x}: {symbol}{suffix}")
        lines.extend(result.crash.corrections)
        lines.extend(result.crash.warnings)
    lines.extend(["", f"Bundle: {results[0].bundle_checksum if results else 'none'}"])
    return "\n".join(lines)


def diagnostic(results: list[Result]) -> str:
    return json.dumps([asdict(result) for result in results], indent=2)


def discord_summary(results: list[Result]) -> tuple[str, str | None]:
    full = render(results)
    # Plain text, escaped before display by the Discord adapter.
    if len(full) <= 1750:
        return full, None
    lines = ["Crash decode — full trace attached."]
    for number, result in enumerate(results, 1):
        detection = result.detection
        status = result.error or (
            f"{detection.build_id or 'choose build'} ({detection.status})"
            if detection
            else "Decode failed"
        )
        lines.append(f"Screenshot {number}: {status[:65]}")
    return "\n".join(lines)[:1750], full
