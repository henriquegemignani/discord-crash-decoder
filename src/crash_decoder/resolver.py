from bisect import bisect_right
from functools import lru_cache
from pathlib import PurePosixPath

from .models import Frame, Resolution


@lru_cache(maxsize=100000)
def demangle(name: str) -> str:
    try:
        from multi_demangle import demangle_symbol as cw_demangle

        return cw_demangle(name) or name
    except (ValueError, IndexError):
        return name


class Resolver:
    def __init__(self, build: dict):
        self.build = build
        self.index = {}
        for binary in build["binaries"]:
            for section in binary["sections"]:
                functions = [f for f in binary["functions"] if f["section"] == section["name"]]
                self.index[(binary["id"], section["name"])] = (
                    [f["address"] for f in functions],
                    functions,
                )

    def resolve(
        self,
        frame: Frame,
        runtime_sections: dict[str, dict[str, int]] | None = None,
        module: str | None = None,
        section: str | None = None,
        module_allocations: dict[str, int] | None = None,
    ) -> Resolution:
        if frame.address % 4:
            return Resolution(frame, reason="Unaligned PowerPC instruction address")
        matches = []
        module_name = module or frame.module
        for binary in self.build["binaries"]:
            if binary["kind"] == "rel":
                if module_name and PurePosixPath(module_name).stem != binary["id"]:
                    continue
                if not module_name and binary["id"] not in (runtime_sections or {}):
                    continue
            elif module_name:
                continue
            for sec in binary["sections"]:
                if not sec["executable"] or (section and section != sec["name"]):
                    continue
                address = frame.address
                if binary["kind"] == "rel":
                    if frame.kind == "module_offset":
                        # RFO is relative to the REL allocation, not the .text section.
                        base = sec.get("file_offset")
                        allocation = (module_allocations or {}).get(binary["id"])
                        runtime = (runtime_sections or {}).get(binary["id"], {}).get(sec["name"])
                        if allocation is not None and runtime is not None:
                            base = runtime - allocation
                            if base < 0:
                                continue
                    else:
                        base = (runtime_sections or {}).get(binary["id"], {}).get(sec["name"])
                    if base is None:
                        continue
                    address -= base
                if not sec["start"] <= address < sec["end"]:
                    continue
                starts, functions = self.index[(binary["id"], sec["name"])]
                pos = bisect_right(starts, address) - 1
                if pos >= 0:
                    func = functions[pos]
                    if address < func["address"] + func["size"]:
                        matches.append((binary, func, address - func["address"]))
        if len(matches) != 1:
            reason = "Outside known function ranges"
            if module_name:
                reason = (
                    "Module/section layout unavailable or address outside known module functions"
                )
            if len(matches) > 1:
                reason = "Conflicting runtime section ranges"
            return Resolution(frame, reason=reason)
        binary, func, offset = matches[0]
        return Resolution(
            frame, demangle(func["name"]), func["name"], offset, binary["id"], func.get("source")
        )
