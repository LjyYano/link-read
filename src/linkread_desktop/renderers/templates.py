"""Fill the bundled HTML templates without reprocessing inserted content."""

from importlib.resources import files
import json
import re


_SLOT = re.compile(r"<!--LR:([A-Z_]+)-->")


def fill_template(name: str, values: dict[str, str]) -> str:
    template = files(__package__).joinpath(name).read_text(encoding="utf-8")
    return _SLOT.sub(lambda match: values[match[1]], template)


def json_data(value: object) -> str:
    """Serialize data for an inert application/json element."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
