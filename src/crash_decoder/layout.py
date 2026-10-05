import json


def parse_layout(text: str) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Validate explicitly supplied runtime REL section/allocation addresses."""
    if not text.strip():
        return {}, {}
    try:
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) - {"sections", "allocations"}:
            raise ValueError

        def address(raw):
            result = int(raw, 0) if isinstance(raw, str) else raw
            if type(result) is not int or not 0 <= result < 2**32:
                raise ValueError
            return result

        sections, allocations = value.get("sections", {}), value.get("allocations", {})
        if not isinstance(sections, dict) or not isinstance(allocations, dict):
            raise ValueError
        for module, entries in sections.items():
            if not isinstance(module, str) or not isinstance(entries, dict):
                raise ValueError
            if any(not isinstance(section, str) for section in entries):
                raise ValueError
        return (
            {
                module: {section: address(base) for section, base in entries.items()}
                for module, entries in sections.items()
            },
            {module: address(base) for module, base in allocations.items()},
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(
            "Invalid REL layout JSON; use sections and allocations with 32-bit addresses."
        ) from exc
