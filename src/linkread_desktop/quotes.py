"""Quote cards tied to consecutive transcript cues."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai.models import MAX_HIGHLIGHT_SPAN, lexical_content
from .models import TranscriptDocument


def cue_text(document: TranscriptDocument, start: int, end: int) -> str:
    return " ".join(cue.text for cue in document.segments[start:end + 1])


def context_bounds(document: TranscriptDocument, start: int, end: int) -> tuple[int, int]:
    return max(0, start - 2), min(end + 2, len(document.segments) - 1)


class QuoteCard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    start_segment_index: int = Field(ge=0)
    end_segment_index: int = Field(ge=0)
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    bold: list[str] = Field(default_factory=list, max_length=2)
    red: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_emphasis(self) -> "QuoteCard":
        for phrase in self.bold + self.red:
            if not lexical_content(phrase) or (phrase not in self.front and phrase not in self.back):
                raise ValueError("加粗和标红必须引用卡片里的连续原文")
        return self

    def verify_source(self, document: TranscriptDocument) -> None:
        start, end = self.start_segment_index, self.end_segment_index
        if not start <= end < len(document.segments) or end - start > MAX_HIGHLIGHT_SPAN:
            raise ValueError("金句字幕索引超出范围")
        if (lexical_content(self.front) != lexical_content(cue_text(document, start, end)) or
                lexical_content(self.back) != lexical_content(cue_text(document, *context_bounds(document, start, end)))):
            raise ValueError("金句或上下文与原始字幕不一致")


class QuoteCards(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[QuoteCard] = Field(default_factory=list)

    def verify_source(self, document: TranscriptDocument) -> None:
        previous_end = -1
        for card in self.cards:
            card.verify_source(document)
            if card.start_segment_index <= previous_end:
                raise ValueError("金句选段必须按顺序排列且互不重叠")
            previous_end = card.end_segment_index
