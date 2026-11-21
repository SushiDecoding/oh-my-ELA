"""Transcript state: markdown styling, the line model, and its lexer."""

from __future__ import annotations

import json
from typing import Any

from prompt_toolkit.lexers import Lexer

from ..tokens import format_int
from ..tools import result_headline, summarize_call
from ..ui import duration, sanitize
from .style import _INLINE

def inline_fragments(text: str) -> list[tuple[str, str]]:
    """Style inline markdown without dropping a single character."""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            out.append(("class:assistant", text[position : match.start()]))
        token = match.group(0)
        if token.startswith("`"):
            out.append(("class:assistant.marker", "`"))
            out.append(("class:code", token[1:-1]))
            out.append(("class:assistant.marker", "`"))
        elif token.startswith("**"):
            out.append(("class:assistant.marker", "**"))
            out.append(("class:bold", token[2:-2]))
            out.append(("class:assistant.marker", "**"))
        else:
            split = token.index("](")
            label = token[1:split]
            url = token[split + 2 : -1]
            out.append(("class:assistant.marker", "["))
            out.append(("class:link", label))
            out.append(("class:assistant.marker", f"]({url})"))
        position = match.end()
    if position < len(text):
        out.append(("class:assistant", text[position:]))
    return out

class TranscriptModel:
    """Line/fragment state machine, independent of prompt_toolkit."""

    def __init__(self) -> None:
        self.lines: list[list[tuple[str, str]]] = [[]]
        self.kinds: list[str] = ["prose"]
        self.stream_line = ""
        self.in_code = False

    # -- streamed assistant output ----------------------------------------
    def _style_stream_line(self) -> list[tuple[str, str]]:
        raw = self.stream_line
        stripped = raw.lstrip()
        if self.in_code:
            if stripped.startswith("```"):
                return [("class:code.frame", raw)]
            return [("class:code", raw)]
        if stripped.startswith("```"):
            return [("class:code.frame", raw)]
        if stripped.startswith("#"):
            return [("class:heading", raw)]
        if stripped.strip() in ("---", "***", "___"):
            return [("class:assistant.marker", raw)]
        return inline_fragments(raw)

    def _finish_line(self) -> str:
        if self.stream_line.lstrip().startswith("```"):
            self.in_code = not self.in_code
        self.lines.append([])
        self.kinds.append("prose")
        self.stream_line = ""
        return "\n"

    def feed(self, delta: str) -> str:
        out: list[str] = []
        parts = delta.split("\n")
        for index, part in enumerate(parts):
            if part:
                self.stream_line += part
                self.lines[-1] = self._style_stream_line()
                out.append(part)
            if index < len(parts) - 1:
                out.append(self._finish_line())
        return "".join(out)

    def finish_request(self) -> str:
        out = self._finish_line() if self.stream_line else ""
        self.in_code = False
        return out

    # -- discrete lines ----------------------------------------------------
    def append_line(
        self,
        fragments: list[tuple[str, str]],
        plain: str,
        kind: str = "prose",
    ) -> str:
        prefix = self._finish_line() if self.stream_line else ""
        self.lines[-1] = fragments
        self.kinds[-1] = kind
        self.lines.append([])
        self.kinds.append("prose")
        return prefix + plain + "\n"

    def append_blank(self) -> str:
        return self.append_line([], "", "blank")

    def reset(self) -> None:
        self.lines = [[]]
        self.kinds = ["prose"]
        self.stream_line = ""
        self.in_code = False

class TranscriptLexer(Lexer):
    def __init__(self, owner: "FullScreenUI"):
        self.owner = owner

    def lex_document(self, document):
        def get_line(lineno: int):
            lines = self.owner.model_state.lines
            if 0 <= lineno < len(lines):
                return lines[lineno]
            return []

        return get_line


class TranscriptMixin:
    """Transcript writes and rendering events."""



    def _insert_at_end(self, text: str) -> None:
        if not text:
            return
        saved = self.transcript_buffer.cursor_position
        self.transcript_buffer.cursor_position = len(self.transcript_buffer.text)
        self.transcript_buffer.insert_text(text)
        if self.follow:
            self.transcript_buffer.cursor_position = len(self.transcript_buffer.text)
        else:
            self.transcript_buffer.cursor_position = saved
        if self._app_running:
            self.app.invalidate()

    def _scroll_to_end(self) -> None:
        self.follow = True
        self.transcript_buffer.cursor_position = len(self.transcript_buffer.text)

    def _scroll_page(self, direction: int) -> None:
        document = self.transcript_buffer.document
        page = max(1, self._term_size()[0] - 8)
        target = max(
            0,
            min(document.line_count - 1, document.cursor_position_row + direction * page),
        )
        if direction > 0 and target >= document.line_count - 1:
            self.follow = True
            self._scroll_to_end()
        else:
            self.follow = False
            self.transcript_buffer.cursor_position = document.translate_row_col_to_index(
                target, 0
            )
        self.app.invalidate()

    def _append_line(
        self,
        fragments: list[tuple[str, str]],
        plain: str,
        kind: str = "prose",
    ) -> None:
        self._insert_at_end(self.model_state.append_line(fragments, plain, kind))
        if self.follow:
            self._scroll_to_end()

    def _append_blank(self) -> None:
        self._insert_at_end(self.model_state.append_blank())
        if self.follow:
            self._scroll_to_end()

    def _append_user(self, text: str) -> None:
        for index, line in enumerate(text.split("\n")):
            line = sanitize(line)
            prefix = "❯ " if index == 0 else "  "
            self._append_line(
                [("class:user.prompt", prefix), ("class:user", line)],
                prefix + line,
                "user",
            )

    def _append_plain(self, text: str) -> None:
        if text == "":
            self._append_blank()
            return
        for line in text.split("\n"):
            line = sanitize(line)
            self._append_line([("class:assistant", line)], line, "plain")

    def _append_info(self, message: str) -> None:
        for line in str(message).split("\n"):
            line = sanitize(line)
            self._append_line([("class:info", "  " + line)], "  " + line, "info")

    def _append_warn(self, message: str) -> None:
        for index, line in enumerate(str(message).split("\n")):
            line = sanitize(line)
            prefix = "  ! " if index == 0 else "    "
            self._append_line([("class:warn", prefix + line)], prefix + line, "warn")

    def _append_error(self, message: str) -> None:
        for index, line in enumerate(str(message).split("\n")):
            line = sanitize(line)
            prefix = "  error " if index == 0 else "        "
            self._append_line([("class:error", prefix + line)], prefix + line, "error")

    def _on_text(self, delta: str) -> None:
        self._insert_at_end(self.model_state.feed(sanitize(delta)))
        if self.follow:
            self._scroll_to_end()

    def _on_request_finish(self) -> None:
        self._insert_at_end(self.model_state.finish_request())
        if self.follow:
            self._scroll_to_end()

    def _on_tool_end(self, name, summary, result, elapsed, ok) -> None:
        self._insert_at_end(self.model_state.finish_request())
        name = sanitize(str(name))
        summary = sanitize(str(summary))
        fragments = [("class:tool.ok" if ok else "class:tool.fail", "  ● ")]
        if name:
            fragments.append(("class:tool.name", name))
            if summary:
                fragments.append(("class:tool", " " + summary))
        else:
            fragments.append(("class:tool", summary))
        fragments.append(("class:tool.meta", " · " + duration(elapsed)))
        if not ok:
            fragments.append(
                ("class:tool.fail", " · " + sanitize(result_headline(result, 70)))
            )
        plain = "".join(text for _, text in fragments)
        self._append_line(fragments, plain, "tool")

        if self.verbose:
            lines = result.splitlines()[:60]
            for line in lines:
                self._append_line(
                    [("class:tool.output", "    " + sanitize(line))],
                    "    " + sanitize(line),
                    "tool-output",
                )
        elif ok:
            headline = sanitize(result_headline(result, 100))
            self._append_line(
                [("class:tool.output", "    " + headline)],
                "    " + headline,
                "tool-output",
            )
        self.status_label = "thinking"
        if self.follow:
            self._scroll_to_end()

    def _on_turn_end(self, elapsed, usage, context_percent, steps, ok) -> None:
        self._insert_at_end(self.model_state.finish_request())
        fragments = [
            (
                "class:status.ok" if ok else "class:status.meta",
                "  ✓" if ok else "  !",
            ),
            ("class:status.meta", " " + duration(elapsed)),
        ]
        if usage is not None and (usage.prompt_tokens or usage.completion_tokens):
            fragments.append(
                (
                    "class:status.meta",
                    f" · {format_int(usage.prompt_tokens)}↑ "
                    f"{format_int(usage.completion_tokens)}↓",
                )
            )
        fragments.append(("class:status.meta", f" · ctx {context_percent}%"))
        if steps > 1:
            fragments.append(("class:status.meta", f" · {steps} steps"))
        plain = "".join(text for _, text in fragments)
        self._append_line(fragments, plain, "status")
        self.context_percent = context_percent
        self.status_label = "ready"
        if self.follow:
            self._scroll_to_end()

    def _finish_task(self, keep_going: bool) -> None:
        self._insert_at_end(self.model_state.finish_request())
        self.busy = False
        self.status_label = "ready"
        self.spin_frame = 0
        if self.follow:
            self._scroll_to_end()
        if not keep_going:
            self.app.exit()

    def _clear_direct(self) -> None:
        self.model_state.reset()
        self.transcript_buffer.text = ""
        self.transcript_buffer.cursor_position = 0
        self.follow = True
        if self._app_running:
            self.app.invalidate()

    def _render_history_direct(self, messages: list[dict[str, Any]]) -> None:
        self._clear_direct()
        if len(messages) > 200:
            self._append_info("(earlier history omitted)")
            messages = messages[-200:]
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "user":
                if str(content or "").startswith("[ela-internal]"):
                    continue
                self._append_user(str(content or ""))
                self._append_blank()
            elif role == "assistant":
                if (
                    isinstance(content, str)
                    and content
                    and not content.startswith("[ela-internal]")
                ):
                    self._insert_at_end(self.model_state.feed(sanitize(content)))
                    self._insert_at_end(self.model_state.finish_request())
                for call in message.get("tool_calls") or []:
                    self._append_history_tool_call(call)
                self._append_blank()
            elif role == "tool":
                text = str(content or "")
                headline = sanitize(result_headline(text, 100))
                self._append_line(
                    [("class:tool.output", "    ↳ " + headline)],
                    "    ↳ " + headline,
                    "tool-output",
                )
        if self.follow:
            self._scroll_to_end()

    def _append_history_tool_call(self, call: dict[str, Any]) -> None:
        function = call.get("function") or {}
        name = function.get("name") or "?"
        raw = function.get("arguments") or ""
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            args = {}
        summary = sanitize(summarize_call(name, args if isinstance(args, dict) else {}))
        fragments = [
            ("class:tool.ok", "  ● "),
            ("class:tool.name", name),
        ]
        if summary:
            fragments.append(("class:tool", " " + summary))
        plain = "".join(text for _, text in fragments)
        self._append_line(fragments, plain, "tool")
