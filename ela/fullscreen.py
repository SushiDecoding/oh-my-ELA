"""Compatibility shim for the full-screen TUI.

The implementation moved to the ela.tui package, split by concern:
style, transcript, layout, modals, login, askuser, goal, and core. This
module re-exports the public names so existing imports keep working.
"""

from __future__ import annotations

from .tui.askuser import AskUserMixin
from .tui.core import FullScreenUI
from .tui.goal import GoalMixin
from .tui.layout import LayoutMixin
from .tui.login import LoginMixin
from .tui.modals import EffortPermissionMixin, PermissionRequest
from .tui.style import MODEL_PAGE, PROVIDER_PAGE, SPINNER, STYLE
from .tui.transcript import TranscriptLexer, TranscriptModel, inline_fragments

__all__ = [
    "AskUserMixin",
    "EffortPermissionMixin",
    "FullScreenUI",
    "GoalMixin",
    "LayoutMixin",
    "LoginMixin",
    "MODEL_PAGE",
    "PROVIDER_PAGE",
    "PermissionRequest",
    "SPINNER",
    "STYLE",
    "TranscriptLexer",
    "TranscriptModel",
    "inline_fragments",
]
