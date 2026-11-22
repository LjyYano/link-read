"""Real offline reader documents for appearance and attribution interaction checks."""
import json
import os
from pathlib import Path

from linkread_desktop.ai.models import NoteDocument
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.quotes import QuoteCard, QuoteCards, context_bounds, cue_text
from linkread_desktop.renderers.extras import ArticleExtras
from linkread_desktop.renderers.html import render_html


REPO = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("LINKREAD_READER_TOOLS_OUT", REPO.parent / "reader-tools-verification")).resolve()
SITE = OUT / "site"


def main():
    SITE.mkdir(parents=True, exist_ok=True)
    sources = {
        "article": SourceInfo(platform="youtube", title="Original episode", video_id="1-Fo8McCD9w", author='Ada <Reader> & Co'),
        "another": SourceInfo(platform="youtube", title="Another saved article", video_id="aaaaaaaaaaa"),
        "apple": SourceInfo(platform="apple_podcasts", title="A saved podcast episode", video_id="1000757475500",
                            url="https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500"),
        "local": SourceInfo(platform="local", title="Local transcript", url="file:///Users/private/captions.srt"),
    }
    for name, source in sources.items():
        source.description = "\n\n".join(f"Publisher section {i}: keep the original explanation and its context. " * 6 for i in range(20))
        source.description_format = "text"
        raw = TranscriptDocument(source=source, transcript_source="local", segments=[
            TranscriptSegment(start_ms=i * 10000, end_ms=i * 10000 + 9000,
                text=f"Topic {i}: keep the original words and the place where you stopped reading. "
                     "This offline fixture exercises the real reader without model or source requests. " * 3)
            for i in range(40)
        ])
        note = NoteDocument.model_validate({"transcript": raw, "provider": "codex", "analysis": {
            "summary": "Check the source and keep your reading context.",
            "keywords": ["source", "context", "reading"],
            "reading_paragraphs": [{"start_segment_index": i, "end_segment_index": i, "text": cue.text}
                                   for i, cue in enumerate(raw.segments)],
            "chapters": [{"start_segment_index": i, "start_ms": i * 10000, "title": title, "summary": "Original source."}
                         for i, title in [(0, "Getting started"), (12, "Attention"), (24, "Practice")]],
        }})
        quote = raw.segments[12].text
        context = cue_text(raw, *context_bounds(raw, 12, 12))
        extras = ArticleExtras(
            mindmap="# Careful reading\n## Attention\n### Listen\n## Context\n### Check the source\n",
            quotes=QuoteCards(cards=[QuoteCard(title="Keep context", start_segment_index=12, end_segment_index=12,
                                             front=quote, back=context, bold=["keep the original words"],
                                             red=["where you stopped reading"])]),
        )
        title = 'My reading title <safe> & "quoted"' if name == "article" else source.title
        markup = render_html(note, extras=extras, title=title)
        (SITE / f"{name}.html").write_text(markup, encoding="utf-8")
    (SITE / "fixture.json").write_text(json.dumps({"quote": quote, "context": context, "title": 'My reading title <safe> & "quoted"',
        "author": 'Ada <Reader> & Co', "time": "02:00–02:09", "source": "https://www.youtube.com/watch?v=1-Fo8McCD9w&t=120s"}), encoding="utf-8")
    print(json.dumps({"site": str(SITE), "model_calls": 0}))


if __name__ == "__main__":
    main()

