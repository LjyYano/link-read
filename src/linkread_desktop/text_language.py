"""Shared language and script detection helpers across translation and AI stages."""

from collections.abc import Iterable
import re

_LANG_TAG = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{1,8})*")


def normalize_language_tag(value: str | None) -> str | None:
    normalized = (value or "").strip().lower().replace("_", "-").removeprefix("ai-")
    return normalized if _LANG_TAG.fullmatch(normalized) else None


def is_english_language(language: str | None) -> bool:
    raw = (language or "").strip().lower()
    if raw == "english":
        return True
    normalized = normalize_language_tag(language)
    return normalized == "en" or (normalized is not None and normalized.startswith("en-"))


def _sample_text(texts: Iterable[str], max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    for text in texts:
        if not text:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        parts.append(text[:remaining])
        total += min(len(text), remaining)
    return "".join(parts)


def looks_english_text(
    texts: Iterable[str], *, max_chars: int = 3000, require_zero_han: bool = False,
) -> bool:
    sample = _sample_text(texts, max_chars)
    if not sample:
        return False
    han = sum(1 for ch in sample if "\u3400" <= ch <= "\u9fff")
    other = any("\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af" for ch in sample)
    latin = sum(1 for ch in sample if "A" <= ch <= "Z" or "a" <= ch <= "z")
    if other or latin < 20:
        return False
    if require_zero_han:
        return han == 0
    return latin > han * 3


def is_latin_heavy(texts: Iterable[str], *, max_chars: int = 4000) -> bool:
    sample = _sample_text(texts, max_chars)
    if not sample:
        return False
    cjk = sum(
        1 for ch in sample
        if "\u3400" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af"
    )
    return cjk / len(sample) < 0.25
