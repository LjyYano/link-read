"""Deterministic, source-complete reader for Escape browser regression checks."""
import json
import os
from pathlib import Path

from linkread_desktop.ai.models import NoteDocument
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.extras import ArticleExtras
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.reading_state import reading_state_key


REPO = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("LINKREAD_ESCAPE_OUT", REPO.parent / "reader-escape-verification")).resolve()
SITE = OUT / "site"


def main():
    SITE.mkdir(parents=True, exist_ok=True)
    raw = TranscriptDocument(source=SourceInfo(platform="local", title="A practical guide to careful reading"),
        transcript_source="local", segments=[TranscriptSegment(start_ms=i * 10000, end_ms=i * 10000 + 9000,
            text=f"Topic {i}: keep the original words and the place where you stopped reading. "
                 "This offline fixture exercises the real reader without model or source requests.") for i in range(36)])
    note = NoteDocument.model_validate({"transcript": raw, "provider": "codex", "analysis": {
        "summary": "Check the source and keep your reading context.",
        "reading_paragraphs": [{"start_segment_index": i, "end_segment_index": i, "text": segment.text}
                               for i, segment in enumerate(raw.segments)],
        "chapters": [{"start_segment_index": i, "start_ms": i * 10000, "title": title, "summary": "Original source."}
                     for i, title in [(0, "Getting started"), (12, "Attention"), (24, "Practice")]],
    }})
    html = render_html(note, extras=ArticleExtras(mindmap="# Careful reading\n## Attention\n### Listen\n#### Notice details\n## Context\n### Check the source\n#### Keep your place\n"))
    preview = (REPO / "desktop/src-tauri/src/preview.html").read_text()
    # Match native preview_document: the bundled Markmap code also contains
    # a literal </body> inside its own HTML template string.
    closing = html.rfind("</body>")
    assert closing >= 0, "rendered reader is missing its closing body"
    html = html[:closing] + preview + "\n" + html[closing:]
    (SITE / "article.html").write_text(html)
    (SITE / "fallback.html").write_text(html.replace("if (window.markmap?.Transformer && window.markmap?.Markmap) {", "if (false) {"))
    data = {"readingKey": reading_state_key(raw), "article": {"id": "escape-fixture", "title": raw.source.title,
        "platform": "local", "sourceUrl": None, "category": "", "hasAnalysis": True, "updatedAt": 0,
        "author": None, "publishedAt": None, "durationMs": 360000, "thumbnailUrl": None,
        "isRead": False, "isStarred": False}}
    (SITE / "fixture.json").write_text(json.dumps(data))
    print(json.dumps({"site": str(SITE), "model_calls": 0}))


if __name__ == "__main__":
    main()
