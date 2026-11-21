"""Permission modes and path confinement.

Modes:
  full      all tools, all paths, no prompts
  ask       reads/search within the workspace; writes/edits/pwsh need approval
  readonly  read/search only, all paths, no prompts
"""

from __future__ import annotations

import threading
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .paths import resolve_path
from .ui import UI


class PermissionMode(str, Enum):
    FULL = "full"  # displayed as Auto Mode
    ASK = "ask"
    READONLY = "readonly"
    AUTOREVIEW = "autoreview"


MODE_LABELS = {
    PermissionMode.FULL: "Auto Mode",
    PermissionMode.ASK: "Ask Mode",
    PermissionMode.READONLY: "Readonly Mode",
    PermissionMode.AUTOREVIEW: "AutoReview Mode",
}

MODE_ORDER = [
    PermissionMode.FULL,
    PermissionMode.ASK,
    PermissionMode.READONLY,
    PermissionMode.AUTOREVIEW,
]

READ_TOOLS = {"read", "search", "task", "askuser"}
WRITE_TOOLS = {"write", "edit"}


def parse_mode(value: str) -> PermissionMode | None:
    normalized = " ".join(str(value).strip().lower().replace("_", "-").replace("-", " ").split())
    aliases = {
        "full": PermissionMode.FULL,
        "full access": PermissionMode.FULL,
        "auto": PermissionMode.FULL,
        "auto mode": PermissionMode.FULL,
        "automode": PermissionMode.FULL,
        "yolo": PermissionMode.FULL,
        "ask": PermissionMode.ASK,
        "ask mode": PermissionMode.ASK,
        "readonly": PermissionMode.READONLY,
        "read only": PermissionMode.READONLY,
        "readonly mode": PermissionMode.READONLY,
        "read only mode": PermissionMode.READONLY,
        "ro": PermissionMode.READONLY,
        "autoreview": PermissionMode.AUTOREVIEW,
        "auto review": PermissionMode.AUTOREVIEW,
        "autoreview mode": PermissionMode.AUTOREVIEW,
        "auto review mode": PermissionMode.AUTOREVIEW,
        "review": PermissionMode.AUTOREVIEW,
    }
    return aliases.get(normalized)


def next_mode(mode: PermissionMode) -> PermissionMode:
    index = MODE_ORDER.index(mode) if mode in MODE_ORDER else 0
    return MODE_ORDER[(index + 1) % len(MODE_ORDER)]


class PermissionManager:
    def __init__(self, cwd: Path, mode: PermissionMode = PermissionMode.ASK):
        self.cwd = cwd.resolve()
        self.mode = mode
        self._approval_lock = threading.Lock()

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode

    def cycle(self) -> PermissionMode:
        self.mode = next_mode(self.mode)
        return self.mode

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode.value)

    def authorize(
        self,
        tool: str,
        args: dict[str, Any],
        summary: str,
        detail: str,
        ui: UI,
        reviewer: Callable[[str, dict[str, Any], str, str], tuple[bool, str]] | None = None,
    ) -> tuple[bool, str]:
        """Return (allowed, error_message). May block on a user approval."""
        mode = self.mode
        if mode == PermissionMode.FULL:
            return True, ""
        if mode == PermissionMode.READONLY:
            if tool in READ_TOOLS:
                return True, ""
            return (
                False,
                "Error: permission denied: Readonly Mode only allows read/search.",
            )

        if mode == PermissionMode.AUTOREVIEW:
            if tool in READ_TOOLS:
                error = self._check_paths(tool, args)
                return (True, "") if not error else (False, error)
            if tool in WRITE_TOOLS:
                error = self._check_paths(tool, args)
                if error:
                    return False, error
                return self._review_or_ask(
                    ui, reviewer, tool, args, summary, detail
                )
            if tool == "pwsh":
                note = (
                    detail
                    + "\nnote: shell commands are not filesystem-sandboxed; "
                    "the safety reviewer decides first and the user is asked "
                    "only when the review is unsafe."
                ).strip()
                return self._review_or_ask(
                    ui, reviewer, tool, args, summary, note
                )
            return False, f"Error: permission denied: unknown tool {tool!r}."

        # Ask Mode
        if tool in READ_TOOLS:
            error = self._check_paths(tool, args)
            return (True, "") if not error else (False, error)
        if tool in WRITE_TOOLS:
            error = self._check_paths(tool, args)
            if error:
                return False, error
            return self._ask(ui, tool, summary, detail)
        if tool == "pwsh":
            note = (
                detail
                + "\nnote: shell commands are approval-gated and are not "
                "filesystem-sandboxed; approve only commands you trust."
            ).strip()
            return self._ask(ui, tool, summary, note)
        return False, f"Error: permission denied: unknown tool {tool!r}."

    def _review_or_ask(
        self,
        ui: UI,
        reviewer,
        tool: str,
        args: dict[str, Any],
        summary: str,
        detail: str,
    ) -> tuple[bool, str]:
        if reviewer is None:
            return self._ask(ui, tool, summary, detail)

        ui.request_start("auto-review")
        try:
            safe, reason = reviewer(tool, args, summary, detail)
        except Exception as exc:
            safe = False
            reason = f"review unavailable: {type(exc).__name__}"
        finally:
            ui.request_finish()

        if safe:
            ui.info(f"[auto-review] allowed: {reason or 'safe'}")
            return True, ""
        ui.info(f"[auto-review] unsafe: {reason or 'needs user approval'}")
        return self._ask(
            ui,
            tool,
            summary,
            (detail + f"\n[auto-review] unsafe: {reason}").strip(),
        )

    def _ask(self, ui: UI, tool: str, summary: str, detail: str) -> tuple[bool, str]:
        # Serialize prompts across the parent agent and parallel subagents.
        with self._approval_lock:
            if self.mode == PermissionMode.FULL:
                return True, ""
            if self.mode == PermissionMode.READONLY:
                return False, "Error: permission denied: Readonly Mode."
            if ui.approve(tool, summary, detail):
                return True, ""
            return False, "Error: permission denied by user."

    def _check_paths(self, tool: str, args: dict[str, Any]) -> str:
        raw_paths: list[Any] = []
        if tool == "read":
            raw = args.get("paths", args.get("path"))
            raw_paths = raw if isinstance(raw, list) else ([raw] if raw else [])
        elif tool == "search":
            raw_paths = [args.get("path") or "."]
        elif tool in WRITE_TOOLS:
            raw_paths = [args.get("path")]
        else:
            return ""

        for raw in raw_paths:
            if not isinstance(raw, str) or not raw:
                continue
            try:
                path = resolve_path(self.cwd, raw)
            except (OSError, ValueError):
                return f"Error: permission denied: invalid path {raw!r}."
            try:
                path.relative_to(self.cwd)
            except ValueError:
                return (
                    "Error: permission denied: "
                    f"{raw} is outside the workspace {self.cwd} "
                    "(ask mode limits file access to the terminal's folder)."
                )
        return ""
