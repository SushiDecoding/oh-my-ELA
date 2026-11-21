"""Full-screen terminal UI, split by concern.

Layout lives in the layout module, transcript state in transcript, the
modals in login / askuser / modals, and core assembles them into
FullScreenUI.
"""

from __future__ import annotations

from .core import FullScreenUI
from .modals import PermissionRequest
from .transcript import TranscriptLexer, TranscriptModel, inline_fragments

__all__ = [
    "FullScreenUI",
    "PermissionRequest",
    "TranscriptLexer",
    "TranscriptModel",
    "inline_fragments",
]
