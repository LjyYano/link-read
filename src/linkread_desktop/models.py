"""Stable JSON contract: source metadata and timestamped transcript segments."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_end(self) -> "TranscriptSegment":
        if self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("end_ms cannot be earlier than start_ms")
        if not self.text.strip():
            raise ValueError("segment text cannot be blank")
        self.text = self.text.strip()
        return self


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["local", "bilibili", "youtube", "apple_podcasts"]
    title: str = Field(min_length=1)
    url: str | None = None
    video_id: str | None = None
    author: str | None = None
    published_at: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    thumbnail_url: str | None = None
    # Original publisher text, never an AI summary. None keeps old archive
    # serialization (and content-derived local identities) unchanged.
    description: str | None = None
    description_format: Literal["text", "html"] | None = None


class TranscriptDocument(BaseModel):
    """Schema v1.0; ASR and AI enrichment will be additive, separate stages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0", "1.1"] = "1.0"
    source: SourceInfo
    transcript_source: Literal["subtitle", "local", "asr"]
    language: str | None = None
    segments: list[TranscriptSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_chronology(self) -> "TranscriptDocument":
        if any(a.start_ms > b.start_ms for a, b in zip(self.segments, self.segments[1:])):
            raise ValueError("segments must be sorted by start_ms")
        return self
