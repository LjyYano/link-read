"""Deterministic, offline-capable outputs for TranscriptDocument v1.0."""

from .html import render_html
from .markdown import render_markdown

__all__ = ["render_html", "render_markdown"]
