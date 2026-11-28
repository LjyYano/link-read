"""Validated offline reading appearance preferences shared by desktop and exported HTML."""

from hashlib import sha256
import json
import re


THEMES = frozenset({"auto", "light", "sepia", "dark"})
FONT_FAMILIES = frozenset({"sans", "serif", "custom"})
LINE_WIDTHS = frozenset({"narrow", "standard", "wide"})
LINE_SPACINGS = frozenset({"compact", "standard", "relaxed"})
DEFAULT_TABS = frozenset({"transcript", "summary"})
MIN_FONT_SIZE = 14
MAX_FONT_SIZE = 26
MAX_CUSTOM_FONT_BYTES = 128

_FORBIDDEN_FONT_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f<>{};:"\'\\/()*]')


def default_reading_preferences() -> dict:
    return {
        "theme": "auto",
        "fontFamily": "sans",
        "customFont": "",
        "fontSize": 16,
        "lineWidth": "standard",
        "lineSpacing": "relaxed",
        "defaultTab": "transcript",
        "showCover": True,
        "showHighlights": True,
    }


def valid_custom_font(name: object) -> bool:
    if not isinstance(name, str):
        return False
    cleaned = name.strip()
    if not cleaned:
        return False
    if len(cleaned.encode("utf-8")) > MAX_CUSTOM_FONT_BYTES:
        return False
    return _FORBIDDEN_FONT_CHARS.search(cleaned) is None


def normalize_reading_preferences(raw: object) -> dict:
    """Return a strictly whitelisted reading preferences dict with safe defaults."""
    result = default_reading_preferences()
    if not isinstance(raw, dict):
        return result
    theme = raw.get("theme")
    if isinstance(theme, str) and theme in THEMES:
        result["theme"] = theme
    font_family = raw.get("fontFamily")
    if isinstance(font_family, str) and font_family in FONT_FAMILIES:
        result["fontFamily"] = font_family
    custom_font = raw.get("customFont")
    if isinstance(custom_font, str):
        cleaned = custom_font.strip()
        if cleaned and valid_custom_font(cleaned):
            result["customFont"] = cleaned
    if result["fontFamily"] == "custom" and not result["customFont"]:
        result["fontFamily"] = "sans"
    font_size = raw.get("fontSize")
    if type(font_size) is int and MIN_FONT_SIZE <= font_size <= MAX_FONT_SIZE:
        result["fontSize"] = font_size
    line_width = raw.get("lineWidth")
    if isinstance(line_width, str) and line_width in LINE_WIDTHS:
        result["lineWidth"] = line_width
    line_spacing = raw.get("lineSpacing")
    if isinstance(line_spacing, str) and line_spacing in LINE_SPACINGS:
        result["lineSpacing"] = line_spacing
    default_tab = raw.get("defaultTab")
    if isinstance(default_tab, str) and default_tab in DEFAULT_TABS:
        result["defaultTab"] = default_tab
    show_cover = raw.get("showCover")
    if type(show_cover) is bool:
        result["showCover"] = show_cover
    show_highlights = raw.get("showHighlights")
    if type(show_highlights) is bool:
        result["showHighlights"] = show_highlights
    return result


def parse_reading_preferences_json(raw: str | None) -> dict:
    if not raw:
        return default_reading_preferences()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return default_reading_preferences()
    return normalize_reading_preferences(parsed)


def reading_preferences_signature(prefs: dict | None) -> str:
    normalized = normalize_reading_preferences(prefs)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()[:20]
