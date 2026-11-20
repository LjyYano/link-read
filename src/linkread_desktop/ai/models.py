"""M3: versioned, self-contained AI notes with verifiable transcript references."""

from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import TranscriptDocument


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_segment_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1500)


class Highlight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_segment_index: int = Field(ge=0)
    end_segment_index: int = Field(ge=0, description="Inclusive end index")
    start_ms: int = Field(ge=0)
    text: str = Field(min_length=1)


def lexical_content(value: str) -> str:
    """Keep every non-punctuation character in order, ignoring layout changes.

    For lossless punctuation editing the words, numbers, and symbols must remain
    byte-for-byte identical. Do not ignore characters with Unicode category S.
    """
    return "".join(ch for ch in value if lexical_character(ch))


def lexical_character(char: str) -> bool:
    return not char.isspace() and not unicodedata.category(char).startswith("P")


def source_emphasis(phrases: list[str], *texts: str, limit: int) -> list[str]:
    """Keep short emphasis suggestions that occur verbatim in the displayed text."""
    verified = []
    for value in phrases:
        phrase = value.strip()
        if (phrase not in verified and len(phrase) <= 60 and lexical_content(phrase)
                and len(phrase.splitlines()) == 1 and any(phrase in text for text in texts)):
            verified.append(phrase)
            if len(verified) == limit:
                break
    return verified


class ReadingParagraph(BaseModel):
    """Continuous source-cue span with punctuation-only editorial changes."""

    model_config = ConfigDict(extra="forbid")
    start_segment_index: int = Field(ge=0)
    end_segment_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    subheading: str | None = Field(default=None, max_length=120)
    edited: bool = True  # False marks a source-text fallback after failed model validation.
    bold: list[str] = Field(default_factory=list, max_length=1)
    red: list[str] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def validate_emphasis(self) -> "ReadingParagraph":
        if any(not lexical_content(phrase) or phrase not in self.text for phrase in self.bold + self.red):
            raise ValueError("reading emphasis must be a continuous phrase in the paragraph")
        return self


class NoteAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    takeaways: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    reading_paragraphs: list[ReadingParagraph] = Field(default_factory=list)


class NoteDocument(BaseModel):
    """Separate artifact; original transcript stays embedded and unmodified."""
    model_config = ConfigDict(extra="forbid")
    document_type: Literal["linkread_note"] = "linkread_note"
    schema_version: Literal["1.0"] = "1.0"
    transcript: TranscriptDocument
    analysis: NoteAnalysis
    provider: Literal["codex", "claude", "api"]
    prompt_version: Literal["1", "2"] = "1"

    @model_validator(mode="after")
    def validate_grounding(self) -> "NoteDocument":
        segments = self.transcript.segments
        count = len(segments)
        previous = -1
        for chapter in self.analysis.chapters:
            idx = chapter.start_segment_index
            if not 0 <= idx < count or chapter.start_ms != segments[idx].start_ms:
                raise ValueError("chapter timestamp/index is not grounded in transcript")
            if idx <= previous:
                raise ValueError("chapters must be in strictly increasing source order")
            previous = idx
        for highlight in self.analysis.highlights:
            a, b = highlight.start_segment_index, highlight.end_segment_index
            if not 0 <= a <= b < count or b - a > 7:
                raise ValueError("highlight segment span invalid")
            exact = " ".join(s.text for s in segments[a:b + 1])
            if highlight.text != exact or highlight.start_ms != segments[a].start_ms:
                raise ValueError("highlight text or timestamp differs from original transcript")
        # A complete reading edition is continuous, never a selective summary.
        # Validate on every load as well as when the provider first returns JSON.
        if self.analysis.reading_paragraphs:
            expected_start = 0
            for paragraph in self.analysis.reading_paragraphs:
                a, b = paragraph.start_segment_index, paragraph.end_segment_index
                if a != expected_start or b < a or b >= count:
                    raise ValueError("reading paragraph source spans must cover every cue exactly once")
                original = "".join(segment.text for segment in segments[a:b + 1])
                if lexical_content(original) != lexical_content(paragraph.text):
                    raise ValueError("reading paragraph differs from source beyond punctuation/whitespace")
                expected_start = b + 1
            if expected_start != count:
                raise ValueError("reading paragraphs must cover the complete original transcript")
        return self


class ProposedChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class ProposedHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0)


class ChunkInsights(BaseModel):
    """Temporary model output uses chunk-local indices, never free-form timestamps."""
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    takeaways: list[str]
    chapters: list[ProposedChapter]
    highlights: list[ProposedHighlight]
    keywords: list[str]
