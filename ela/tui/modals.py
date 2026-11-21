"""Effort and permission modals, plus the permission request record."""

from __future__ import annotations

import threading

from prompt_toolkit.key_binding import KeyBindings

class PermissionRequest:
    def __init__(self, tool: str, summary: str, detail: str):
        self.tool = tool
        self.summary = summary
        self.detail = detail
        self.event = threading.Event()
        self.allowed = False


class EffortPermissionMixin:
    """Effort picker and permission approval modals."""



    def _open_effort(self, effort: str, ultracode: bool, on_apply) -> None:
        self.effort_draft_level = (
            effort if effort in ("low", "medium", "high") else "medium"
        )
        self.effort_draft_ultracode = bool(ultracode)
        self.effort_callback = on_apply
        self.effort_open = True
        self.app.layout.focus(self.effort_body)
        self.app.invalidate()

    def _effort_fragments(self) -> list[tuple[str, str]]:
        lines = [
            ("class:modal.title", "  Thinking depth\n"),
            ("class:modal.sep", "  " + "─" * 46 + "\n"),
        ]
        for level in ("low", "medium", "high"):
            selected = level == self.effort_draft_level
            marker = "  ▸ " if selected else "    "
            style = "class:modal.selected" if selected else "class:modal.text"
            lines.append((style, f"{marker}{level}\n"))
        mark = "x" if self.effort_draft_ultracode else " "
        lines.append(("class:modal.text", f"\n  [{mark}] ultracode   (Tab)\n"))
        lines.append(
            ("class:modal.hint", "  Enter apply · Esc cancel\n")
        )
        return lines

    def _effort_bindings(self) -> KeyBindings:
        kb = KeyBindings()
        levels = ("low", "medium", "high")

        def move(delta: int) -> None:
            index = (
                levels.index(self.effort_draft_level)
                if self.effort_draft_level in levels
                else 1
            )
            self.effort_draft_level = levels[(index + delta) % len(levels)]
            self.app.invalidate()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            move(-1)

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            move(1)

        @kb.add("tab")
        def _toggle_ultracode(event):
            self.effort_draft_ultracode = not self.effort_draft_ultracode
            self.app.invalidate()

        @kb.add("1")
        def _low(event):
            self.effort_draft_level = "low"
            self.app.invalidate()

        @kb.add("2")
        def _medium(event):
            self.effort_draft_level = "medium"
            self.app.invalidate()

        @kb.add("3")
        def _high(event):
            self.effort_draft_level = "high"
            self.app.invalidate()

        @kb.add("enter")
        def _apply(event):
            self.effort_open = False
            level = self.effort_draft_level
            ultracode = self.effort_draft_ultracode
            self.effort_level = level
            self.ultracode = ultracode
            callback = self.effort_callback
            self.effort_callback = None
            self.app.layout.focus(self.input_window)
            self.app.invalidate()
            if callback is not None:
                callback(level, ultracode)

        @kb.add("escape")
        @kb.add("c-c")
        def _cancel(event):
            self.effort_open = False
            self.effort_callback = None
            self.app.layout.focus(self.input_window)
            self.app.invalidate()

        return kb

    def _permission_fragments(self) -> list[tuple[str, str]]:
        request = self.permission_current
        if request is None:
            return [("class:modal.text", "")]
        lines = [
            ("class:modal.title", "  Permission required\n"),
            ("class:modal.sep", "  " + "─" * 54 + "\n"),
            ("class:modal.text", f"  tool: {request.tool}\n"),
            ("class:modal.text", f"  {request.summary}\n"),
        ]
        for line in request.detail.splitlines()[:6]:
            lines.append(("class:modal.detail", "    " + line[:160] + "\n"))
        lines.append(("class:modal.hint", "\n  [y] allow    [n] deny\n"))
        return lines

    def _permission_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        def resolve(allowed: bool) -> None:
            request = self.permission_current
            if request is not None:
                request.allowed = allowed
                request.event.set()
            self.permission_current = None
            self.permission_open = False
            self.app.invalidate()
            self._show_next_permission()

        @kb.add("y")
        @kb.add("Y")
        def _allow(event):
            resolve(True)

        @kb.add("n")
        @kb.add("N")
        @kb.add("escape")
        @kb.add("c-c")
        def _deny(event):
            resolve(False)

        return kb

    def _show_next_permission(self) -> None:
        if self.permission_current is not None:
            return
        with self.permission_lock:
            if self.permission_queue:
                self.permission_current = self.permission_queue.pop(0)
        if self.permission_current is not None:
            self.permission_open = True
            self.app.layout.focus(self.permission_body)
        else:
            self.permission_open = False
            self.app.layout.focus(self.input_window)
        self.app.invalidate()
