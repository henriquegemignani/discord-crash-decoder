from .detection import detect
from .images import recognize
from .models import Detection, Resolution, Result
from .parser import parse
from .resolver import Resolver


class Decoder:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.builds = {b["id"]: b for b in bundle["builds"]}
        self.resolvers = {}

    def text(
        self,
        text: str,
        label: str = "Text",
        selected: str | None = None,
        original_text: str | None = None,
        runtime_sections: dict[str, dict[str, int]] | None = None,
        module_allocations: dict[str, int] | None = None,
    ) -> Result:
        crash = parse(text, original_text=original_text)
        crash.runtime_sections = runtime_sections or {}
        crash.module_allocations = module_allocations or {}
        detection = detect(crash, list(self.builds.values()), selected)
        result = Result(label, crash, detection, bundle_checksum=self.bundle["checksum"])
        if not crash.readable:
            result.error = "Unreadable or unrelated image: no complete crash addresses found."
        frames = ([crash.ip] if crash.ip else []) + crash.frames
        if detection.build_id:
            resolver = self.resolvers.get(detection.build_id)
            if resolver is None:
                resolver = Resolver(self.builds[detection.build_id])
                self.resolvers[detection.build_id] = resolver
            result.resolutions = [
                resolver.resolve(frame, runtime_sections, module_allocations=module_allocations)
                for frame in frames
            ]
            if runtime_sections:
                detection.evidence.append(
                    "User supplied runtime REL layout; verify it belongs to this crash."
                )
        else:
            result.resolutions = [
                Resolution(frame, reason="Select a build to resolve") for frame in frames
            ]
        return result

    def image(self, data: bytes, label: str) -> Result:
        ocr = recognize(data)
        result = self.text(ocr.text, label, original_text=ocr.original_text)
        result.crash.ocr_passes = ocr.passes
        builds = set()
        for item in ocr.passes:
            detection = detect(parse(item["text"]), list(self.builds.values()))
            if detection.build_id:
                builds.add(detection.build_id)
        if len(builds) > 1:
            result.detection = Detection(
                "unknown",
                None,
                sorted(builds),
                ["OCR passes disagree about the full build identity; choose a map."],
            )
            result.resolutions = [
                Resolution(r.frame, reason="Conflicting OCR build evidence")
                for r in result.resolutions
            ]
        if ocr.text != ocr.original_text:
            result.crash.corrections.append(
                "Header and address regions selected from recorded OCR passes."
            )
        return result
