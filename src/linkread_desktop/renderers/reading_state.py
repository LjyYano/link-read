"""Stable browser reading identities, independent of mutable metadata."""

from hashlib import sha256
import json

from ..ai.models import NoteDocument
from ..models import TranscriptDocument
from .formatting import source_link


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
