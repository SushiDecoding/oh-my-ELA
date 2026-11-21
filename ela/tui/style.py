"""Colour style table and shared layout constants for the TUI."""

from __future__ import annotations

import re

from prompt_toolkit.styles import Style

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PROVIDER_PAGE = 8
MODEL_PAGE = 8
STYLE = Style.from_dict(
    {
        "header": "#d0d0d0",
        "header.title": "#5fd7af bold",
        "header.meta": "#808080",
        "header.logo": "#5fd7af bold",
        "footer": "#808080",
        "footer.accent": "#5fd787",
        "footer.busy": "#ffd75f",
        "footer.warn": "#ffd75f",
        "separator": "#3a3a3a",
        "transcript": "#d0d0d0",
        "input": "#d0d0d0",
        "input.prompt": "#5fd7af bold",
        "user": "#5fd7af",
        "user.prompt": "#5fd7af bold",
        "assistant": "#d0d0d0",
        "assistant.marker": "#585858",
        "bold": "bold",
        "link": "underline #87d7ff",
        "heading": "bold #ffffff",
        "code": "#87d7ff",
        "code.frame": "#585858",
        "tool": "#9e9e9e",
        "tool.name": "#ffffff",
        "tool.ok": "#5fd787",
        "tool.fail": "#ff5f5f",
        "tool.meta": "#585858",
        "tool.output": "#6c6c6c",
        "info": "#808080",
        "warn": "#ffd75f",
        "error": "#ff5f5f",
        "status.ok": "#5fd787",
        "status.meta": "#808080",
        "scrollbar.background": "#303030",
        "scrollbar.button": "#808080",
        "completion-menu": "bg:#1b1b1b",
        "completion-menu.completion": "bg:#1b1b1b #e8e8e8",
        "completion-menu.completion.current": "bg:#2d5f5f #ffffff bold",
        "completion-menu.meta.completion": "bg:#1b1b1b #9a9a9a",
        "completion-menu.meta.completion.current": "bg:#2d5f5f #d8d8d8",
        "completion-menu.scrollbar.background": "bg:#1b1b1b",
        "completion-menu.scrollbar.button": "bg:#606060",
        "text-area.prompt": "#5fd7af bold",
        "footer.full": "#ff5f5f bold",
        "footer.ask": "#ffd75f",
        "footer.readonly": "#5fd787",
        "footer.autoreview": "#87d7ff bold",
        "footer.ultra": "#d787ff bold",
        "footer.goal": "#d787ff",
        "modal": "#e8e8e8",
        "modal.title": "#5fd7af bold",
        "modal.sep": "#3a3a3a",
        "modal.text": "#e8e8e8",
        "modal.selected": "#5fd7af bold",
        "modal.hint": "#808080",
        "modal.detail": "#b8b8b8",
        "modal.label": "#a0a0a0",
        "modal.error": "#ff6b6b",
        "login.input": "#ffffff",
        "goal.title": "#5fd7af bold",
        "goal.task": "#d0d0d0",
        "goal.ok": "#5fd787",
        "goal.fail": "#ff5f5f",
        "goal.meta": "#808080",
    }
)
_INLINE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))")
