"""LinkRead: structured transcripts as a reusable, versioned data layer."""

from .models import SourceInfo, TranscriptDocument, TranscriptSegment
from .pipeline import extract

__all__ = ["SourceInfo", "TranscriptDocument", "TranscriptSegment", "extract"]
__version__ = "0.3.2"
