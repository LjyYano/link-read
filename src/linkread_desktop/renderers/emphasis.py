"""Render verifiable inline emphasis as escaped HTML."""

from html import escape

from ..ai.models import lexical_character, lexical_content


def render_emphasis(text: str, bold: list[str], red: list[str], *, match_lexical: bool = False) -> str:
    if not bold and not red:
        return escape(text)
    marks = []
    if match_lexical:
        positions = [index for index, char in enumerate(text) if lexical_character(char)]
        lexical = "".join(text[i] for i in positions)
    for phrases, is_red in ((bold, False), (red, True)):
        for phrase in phrases:
            start = text.find(phrase)
            if start >= 0:
                marks.append((start, start + len(phrase), is_red))
            elif match_lexical:
                plain_phrase = lexical_content(phrase)
                offset = lexical.find(plain_phrase)
                if plain_phrase and offset >= 0:
                    marks.append((positions[offset], positions[offset + len(plain_phrase) - 1] + 1, is_red))
    points = sorted({0, len(text), *(point for start, end, _ in marks for point in (start, end))})
    parts = []
    for left, right in zip(points, points[1:]):
        fragment = escape(text[left:right])
        active = [is_red for start, end, is_red in marks if start <= left and right <= end]
        if active:
            style = ' class="red"' if any(active) else ''
            fragment = f"<strong{style}>{fragment}</strong>"
        parts.append(fragment)
    return "".join(parts)
