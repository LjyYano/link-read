"""Fill the bundled HTML templates without reprocessing inserted content."""

from importlib.resources import files
import json
import re

from .labels import SHELF_EN, ZH


_SLOT = re.compile(r"<!--LR:([A-Z_]+)-->")


_LABEL_PATTERN = re.compile(
    r'(?P<attribute>(?:aria-label|title|placeholder)="(?P<label>[^"]*)")|(?P<text>(?<=>)[^<>]+(?=<))'
)


def fill_template(name: str, values: dict[str, str], *, language: str = "en") -> str:
    template = files(__package__).joinpath(name).read_text(encoding="utf-8")
    labels = ZH if language == "zh-CN" else SHELF_EN if name == "home.html" and language == "en" else None
    if labels is not None:
        def localize(match):
            if match["label"] is not None:
                label = match["label"]
                return match[0].replace('="' + label + '"', '="' + labels.get(label, label) + '"', 1)
            content = match["text"]
            return labels.get(content, content)

        template = _LABEL_PATTERN.sub(localize, template)
    return _SLOT.sub(lambda match: values[match[1]], template)


def json_data(value: object) -> str:
    """Serialize data for an inert application/json element."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
