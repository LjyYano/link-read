"""Stable browser reading identities, independent of mutable metadata."""

from hashlib import sha256
import json
from struct import iter_unpack

from ..ai.models import NoteDocument
from ..models import TranscriptDocument
from .formatting import source_link


def position_fingerprint(text: str) -> str:
    """Match the reader's p1 fingerprint over JavaScript UTF-16 code units."""
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    first, second = 2166136261, 3339675911
    for (unit,) in iter_unpack("<H", encoded):
        first = ((first ^ unit) * 16777619) & 0xFFFFFFFF
        second = ((second ^ unit) * 2246822519) & 0xFFFFFFFF
    return f"p1-{first:08x}{second:08x}-{len(encoded) // 2}"


def reading_state_key(document: TranscriptDocument | NoteDocument) -> str:
    transcript = document.transcript if isinstance(document, NoteDocument) else document
    canonical = source_link(transcript)
    if canonical:
        if transcript.source.platform == "apple_podcasts":
            from ..links import apple_podcast_ids
            show_id, episode_id, _ = apple_podcast_ids(canonical)
            return f"apple_podcasts:{show_id}:{episode_id}"
        return canonical
    # The original local source identity and cues survive title/description
    # enrichment and display-only cleanup. Do not hash mutable SourceInfo data.
    payload = {
        "platform": transcript.source.platform,
        "video_id": transcript.source.video_id,
        "segments": [item.model_dump(mode="json", exclude_none=True) for item in transcript.segments],
    }
    fingerprint = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"local:{fingerprint}"
