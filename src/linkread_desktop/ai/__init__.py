"""Optional AI enrichment. The transcript extractor never imports providers."""

from .models import NoteDocument, NoteAnalysis
from .pipeline import enrich

__all__ = ["NoteDocument", "NoteAnalysis", "enrich"]
