"""The askuser modal."""

from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings

from ..tools import one_line
from ..ui import sanitize


class AskUserMixin:
    """The askuser modal."""

    def _askuser_fragments(self) -> list[tuple[str, str]]:
        question = one_line(sanitize(self.askuser_question), 160)
        lines = [
            ("class:modal.title", "  " + question + "\n"),
            ("class:modal.sep", "  " + "─" * 56 + "\n"),
        ]
        for index, option in enumerate(self.askuser_options):
            selected = (not self.askuser_other) and index == self.askuser_index
            marker = "▸ " if selected else "  "
            style = "class:modal.selected" if selected else "class:modal.text"
            lines.append(
                (style, f"  {marker}{one_line(sanitize(option), 50)}\n")
            )
        selected = (
            not self.askuser_other
            and self.askuser_index == len(self.askuser_options)
        )
        marker = "▸ " if selected else "  "
        style = "class:modal.selected" if selected else "class:modal.text"
        lines.append((style, f"  {marker}Other… (type your answer)\n"))
        return lines

    def _askuser_hint_fragments(self) -> list[tuple[str, str]]:
        if self.askuser_other:
            text = "  Enter send · Esc back"
        else:
            text = "  ↑/↓ select · Enter confirm · Esc cancel"
        return [("class:modal.hint", text)]

    def _move_askuser(self, delta: int) -> None:
        self.askuser_index = max(
            0,
            min(
                len(self.askuser_options),
                self.askuser_index + delta,
            ),
        )
        self.app.invalidate()

    def _askuser_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            self._move_askuser(-1)

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            self._move_askuser(1)

        @kb.add("enter")
        def _confirm(event):
            if self.askuser_index < len(self.askuser_options):
                self._finish_askuser(
                    self.askuser_options[self.askuser_index]
                )
                return
            self.askuser_other = True
            self.askuser_custom_buffer.text = ""
            self.app.layout.focus(self.askuser_input_window)
            self.app.invalidate()

        @kb.add("escape")
        @kb.add("c-c")
        def _cancel(event):
            self._finish_askuser("User cancelled the question.")

        return kb

    def _askuser_input_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _send(event):
            text = self.askuser_custom_buffer.text.strip()
            if text:
                self._finish_askuser(text)

        @kb.add("escape")
        def _back(event):
            self.askuser_other = False
            self.app.layout.focus(self.askuser_options_window)
            self.app.invalidate()

        @kb.add("c-c")
        def _cancel(event):
            self._finish_askuser("User cancelled the question.")

        return kb

    def _open_askuser(self, question, options, event, result) -> None:
        self.askuser_question = str(question)
        self.askuser_options = [str(option) for option in options]
        self.askuser_index = 0
        self.askuser_other = False
        self.askuser_event = event
        self.askuser_result = result
        self.askuser_custom_buffer.text = ""
        self.askuser_open = True
        self.app.layout.focus(self.askuser_options_window)
        self.app.invalidate()

    def _finish_askuser(self, answer: str) -> None:
        if self.askuser_result is not None:
            self.askuser_result["value"] = answer
        if self.askuser_event is not None:
            self.askuser_event.set()
        self.askuser_open = False
        self.askuser_event = None
        self.askuser_result = None
        self.askuser_custom_buffer.text = ""
        self.app.layout.focus(self.input_window)
        self.app.invalidate()
