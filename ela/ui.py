"""Terminal presentation.

A quiet, dense UI: one-line tool events, framed code blocks, a live spinner,
and a prompt-toolkit input line with history and slash completion.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from pathlib import Path

from . import __version__
from .config import HISTORY_PATH, shorten_cwd
from .tools import one_line, result_headline

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
UNDERLINE = "\x1b[4m"

FG_DIM = "\x1b[38;5;244m"
FG_FAINT = "\x1b[38;5;240m"
FG_ACCENT = "\x1b[38;5;45m"
FG_GREEN = "\x1b[38;5;42m"
FG_RED = "\x1b[38;5;203m"
FG_YELLOW = "\x1b[38;5;221m"
FG_BLUE = "\x1b[38;5;75m"
FG_MAGENTA = "\x1b[38;5;177m"
FG_WHITE = "\x1b[38;5;252m"

_CONTROL = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
    r"|[\x00-\x08\x0b-\x1f\x7f]"
)


def sanitize(text: str) -> str:
    """Strip terminal control sequences from untrusted tool/model output."""
    return _CONTROL.sub("", text)

LOGO_LINES = [
    r"  ___ _    _",
    r" / __| |  /_\ ",
    r" | (__| |_/ _ \ ",
    r" \___|____/_/ \_\ ",
]

COMMANDS: list[tuple[str, str]] = [
    ("/help", "show commands"),
    ("/login", "pick an endpoint, then choose a model"),
    ("/model", "list or switch model"),
    ("/effort", "thinking depth and ultracode"),
    ("/maxstep", "cap tool steps; off = unlimited"),
    ("/permissions", "Auto / Ask / Readonly / AutoReview"),
    ("/goal", "plan and execute one goal, then finish"),
    ("/clear", "clear conversation"),
    ("/compact", "summarize history"),
    ("/session", "session and context info"),
    ("/resume", "resume a past session"),
    ("/export", "export transcript as Markdown"),
    ("/verbose", "toggle full tool output"),
    ("/status", "configuration and prompt budget"),
    ("/exit", "quit"),
]


def enable_ansi() -> None:
    """Enable VT sequences on Windows and force UTF-8 on the std streams."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            for handle in (-11, -12):  # std out, std err
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("ELA_NO_COLOR"):
        return False
    return sys.stdout.isatty()


def paint(text: str, *codes: str, enabled: bool = True) -> str:
    if not enabled or not codes:
        return text
    return "".join(codes) + text + RESET


def duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"


def terminal_width(default: int = 100) -> int:
    return shutil.get_terminal_size((default, 24)).columns


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str, enabled: bool = True):
        self.label = label
        self.enabled = enabled and sys.stdout.isatty()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        index = 0
        while not self._stop.wait(0.08):
            frame = self.FRAMES[index % len(self.FRAMES)]
            line = f"\r  {FG_ACCENT}{frame}{RESET} {FG_DIM}{self.label}…{RESET}"
            sys.stdout.write(line + "\x1b[K")
            sys.stdout.flush()
            index += 1

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=0.5)
        self._thread = None
        if self.enabled:
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()


class MarkdownStream:
    """Line-buffered, lightweight Markdown renderer.

    Complete lines get inline styling and fenced code blocks get a frame.
    Very long lines are flushed raw so output never stalls mid-paragraph.
    """

    def __init__(self, write, enabled: bool = True):
        self.write = write
        self.enabled = enabled
        self.buffer = ""
        self.in_code = False
        self.code_lang = ""
        self.plain_line = False
        self.ended_with_newline = True

    def feed(self, text: str) -> None:
        self.buffer += text
        while True:
            newline = self.buffer.find("\n")
            if newline < 0:
                break
            line, self.buffer = self.buffer[:newline], self.buffer[newline + 1 :]
            if self.plain_line:
                self.write("\n")
                self.plain_line = False
                self.ended_with_newline = True
                continue
            self._render_line(line)
        # Long unbroken line: show it now, skip inline formatting for it.
        if len(self.buffer) > 180:
            self.write(sanitize(self.buffer))
            self.buffer = ""
            self.plain_line = True
            self.ended_with_newline = False

    def flush(self) -> None:
        if self.buffer:
            if self.plain_line:
                self.write(sanitize(self.buffer))
                self.ended_with_newline = False
            else:
                self._render_line(self.buffer)
            self.buffer = ""
        if self.in_code:
            self.write(paint("  ╰─", FG_FAINT, enabled=self.enabled) + "\n")
            self.in_code = False
            self.ended_with_newline = True
        if not self.ended_with_newline:
            self.write("\n")
            self.ended_with_newline = True
        self.plain_line = False

    def _render_line(self, line: str) -> None:
        line = sanitize(line)
        if self.in_code:
            if line.strip().startswith("```"):
                self.write(paint("  ╰─", FG_FAINT, enabled=self.enabled) + "\n")
                self.in_code = False
                self.ended_with_newline = True
                return
            self._render_code_line(line)
            return

        stripped = line.strip()
        if stripped.startswith("```"):
            self.code_lang = stripped[3:].strip()
            label = f"  ╭─ {self.code_lang}" if self.code_lang else "  ╭─"
            self.write(paint(label, FG_FAINT, enabled=self.enabled) + "\n")
            self.in_code = True
            self.ended_with_newline = True
            return

        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            self.write(paint(heading, BOLD, FG_WHITE, enabled=self.enabled) + "\n")
            self.ended_with_newline = True
            return

        if stripped in ("---", "***", "___"):
            self.write(paint("  " + "─" * min(terminal_width() - 4, 60), FG_FAINT, enabled=self.enabled) + "\n")
            self.ended_with_newline = True
            return

        self.write(self._inline(line) + "\n")
        self.ended_with_newline = True

    def _render_code_line(self, line: str) -> None:
        bar = paint("  │ ", FG_FAINT, enabled=self.enabled)
        body = paint(line, FG_BLUE, enabled=self.enabled) if self.enabled else line
        self.write(bar + body + "\n")
        self.ended_with_newline = True

    _INLINE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))")

    def _inline(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        out = []
        position = 0
        for match in self._INLINE.finditer(text):
            out.append(text[position : match.start()])
            token = match.group(0)
            if token.startswith("`"):
                out.append(paint(token[1:-1], FG_BLUE))
            elif token.startswith("**"):
                out.append(paint(token[2:-2], BOLD))
            else:
                label, url = token[1:-1].split("](", 1)
                out.append(paint(label, UNDERLINE) + paint(f" ({url})", FG_DIM))
            position = match.end()
        out.append(text[position:])
        return "".join(out)


def make_slash_completer():
    from prompt_toolkit.completion import Completer, Completion

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/") or " " in text or "\n" in text:
                return
            for name, description in COMMANDS:
                if name.startswith(text):
                    yield Completion(
                        name,
                        start_position=-len(text),
                        display=name,
                        display_meta=description,
                    )

    return SlashCompleter()


class UI:
    """No-op interface; subclasses render events."""

    verbose = False
    supports_modal_ask = True
    supports_modal_effort = False
    supports_permission_modal = False
    supports_login_modal = False
    supports_model_picker = False

    def turn_start(self) -> None: ...
    def request_start(self, label: str = "thinking") -> None: ...
    def text(self, delta: str) -> None: ...
    def request_finish(self) -> None: ...
    def tool_start(self, name: str, summary: str) -> None: ...
    def tool_end(
        self, name: str, summary: str, result: str, elapsed: float, ok: bool
    ) -> None: ...
    def turn_end(
        self, elapsed: float, usage, context_percent: int, steps: int, ok: bool = True
    ) -> None: ...
    def info(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def set_context(self, percent: int, tokens: int) -> None: ...
    def set_model(self, model: str) -> None: ...
    def clear_transcript(self) -> None: ...
    def load_history(self, messages) -> None: ...
    def println(self, text: str = "") -> None: ...
    def set_permission(self, mode: str) -> None: ...
    def set_effort(self, effort: str, ultracode: bool) -> None: ...
    def open_effort(self, effort: str, ultracode: bool, on_apply) -> None: ...
    def goal_start(self, goal: str) -> None:
        self.info(f"goal · {one_line(goal, 100)}")

    def goal_plan(self, goal: str, tasks) -> None:
        self.info(f"goal plan · {len(tasks)} tasks")
        for task in tasks:
            self.info(f"  [ ] {task['id']}. {task['title']}")

    def goal_task_start(self, index: int, total: int, title: str) -> None:
        self.info(f"goal task {index}/{total} · {title}")

    def goal_task_end(self, index, total, title, ok, elapsed, summary) -> None:
        mark = "x" if ok else "!"
        self.info(
            f"  [{mark}] {index}. {title} · {duration(elapsed)} · "
            f"{one_line(summary, 120)}"
        )

    def goal_finish(self, ok: bool, completed: int, total: int) -> None:
        self.info(
            f"goal {'complete' if ok else 'incomplete'} · "
            f"{completed}/{total} tasks"
        )
    def set_logged_in(self, logged_in: bool) -> None: ...
    def open_login(
        self,
        on_submit,
        on_model,
        base_url: str = "",
        current_model: str = "",
    ) -> None: ...
    def open_model_picker(
        self,
        models,
        current: str,
        on_select,
        note: str = "session only",
    ) -> None: ...
    def ask_user(self, question: str, options: list[str]) -> str:
        return "No user available to answer."

    def approve(self, tool: str, summary: str, detail: str) -> bool:
        return False

    def ask(self, question: str) -> str:
        try:
            return input(question)
        except (EOFError, KeyboardInterrupt):
            return ""


class NullUI(UI):
    pass


class PlainUI(UI):
    """One-shot mode: assistant text on stdout, activity on stderr."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._wrote_text = False
        self._last_char = ""

    def turn_start(self) -> None:
        self._wrote_text = False
        self._last_char = ""

    def text(self, delta: str) -> None:
        sys.stdout.write(delta)
        sys.stdout.flush()
        self._wrote_text = True
        if delta:
            self._last_char = delta[-1]

    def request_finish(self) -> None:
        if self._wrote_text and self._last_char != "\n":
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._wrote_text = False
        self._last_char = ""

    def tool_end(self, name, summary, result, elapsed, ok) -> None:
        marker = "ok" if ok else "failed"
        sys.stderr.write(f"[{name}] {sanitize(summary)} · {duration(elapsed)} · {marker}\n")
        if self.verbose:
            for line in result.splitlines()[:40]:
                sys.stderr.write(f"  {sanitize(line)}\n")
        elif not ok:
            sys.stderr.write(f"  {sanitize(result_headline(result))}\n")
        sys.stderr.flush()

    def error(self, message: str) -> None:
        sys.stderr.write(f"ela: {sanitize(str(message))}\n")
        sys.stderr.flush()

    def warn(self, message: str) -> None:
        sys.stderr.write(f"ela: {sanitize(str(message))}\n")
        sys.stderr.flush()

    def info(self, message: str) -> None:
        sys.stderr.write(f"{sanitize(str(message))}\n")
        sys.stderr.flush()


class TerminalUI(UI):
    def __init__(
        self, cwd: Path, model: str, verbose: bool = False, use_color: bool | None = None
    ):
        self.cwd = cwd
        self.model = model
        self.verbose = verbose
        self.use_color = color_enabled() if use_color is None else use_color
        self.context_percent = 0
        self.context_tokens = 0
        self.permission_label = "ask"
        self.effort_level = "medium"
        self.ultracode = False
        self.logged_in = True
        self.on_permission_cycle = None
        self._spinner: Spinner | None = None
        self._stream: MarkdownStream | None = None
        self._session = None
        self._plain_input = False
        self._lock = threading.Lock()

    # -- low level ---------------------------------------------------------
    def _paint(self, text: str, *codes: str) -> str:
        return paint(text, *codes, enabled=self.use_color)

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    def _write(self, text: str) -> None:
        with self._lock:
            sys.stdout.write(text)
            sys.stdout.flush()

    def println(self, text: str = "") -> None:
        self._stop_spinner()
        self._write(text + "\n")

    # -- banner / prompt ---------------------------------------------------
    def banner(self) -> None:
        self._banner_shown = True
        self.println()
        for line in LOGO_LINES:
            self.println(self._paint(line, FG_ACCENT))
        self.println()
        self.println(self._paint(f"  oh-my-elagent  {__version__}", FG_DIM))
        self.println(self._paint(f"  {shorten_cwd(self.cwd)}", FG_DIM))
        if not self.logged_in:
            self.println(
                self._paint("  not logged in · /login to configure", FG_YELLOW)
            )
        self.println()
        hint = "/help commands · ctrl+j newline · ctrl+d exit"
        self.println(self._paint("  " + hint, FG_FAINT))

    def prompt(self) -> str:
        session = self._prompt_session()
        if session is None:
            sys.stdout.write(self._paint("❯ ", BOLD))
            sys.stdout.flush()
            return input()
        return session.prompt(
            [("class:prompt", "❯ ")],
            prompt_continuation=[("class:prompt", "  ")],
        )

    def _prompt_session(self):
        if self._plain_input:
            return None
        if self._session is not None:
            return self._session
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
        from prompt_toolkit.formatted_text import FormattedText

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event):
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-j")
        def _newline_ctrl(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("s-tab")
        def _cycle_permission(event):
            if self.on_permission_cycle is not None:
                self.on_permission_cycle()

        style = Style.from_dict(
            {
                "prompt": "#5fd7af bold",
                "bottom-toolbar": "#8a8a8a",
                "completion-menu": "bg:#1b1b1b",
                "completion-menu.completion": "bg:#1b1b1b #e8e8e8",
                "completion-menu.completion.current": "bg:#2d5f5f #ffffff bold",
                "completion-menu.meta.completion": "bg:#1b1b1b #9a9a9a",
                "completion-menu.meta.completion.current": "bg:#2d5f5f #d8d8d8",
                "completion-menu.scrollbar.background": "bg:#1b1b1b",
                "completion-menu.scrollbar.button": "bg:#606060",
                "auto-suggest": "#585858",
            }
        )

        def toolbar():
            verbose = " · verbose" if self.verbose else ""
            right = f"ctx {self.context_percent}%{verbose}"
            width = terminal_width()
            mode = f"{self.permission_label} · {self.effort_level}"
            if self.ultracode:
                mode += " · ultra"
            identity = self.model if self.logged_in else "not logged in"
            left = f" {identity} · {mode} · {shorten_cwd(self.cwd, 40)}"
            gap = max(2, width - len(left) - len(right) - 2)
            return FormattedText([("class:bottom-toolbar", left + " " * gap + right + " ")])

        try:
            self._session = PromptSession(
                history=FileHistory(str(HISTORY_PATH)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=make_slash_completer(),
                complete_while_typing=True,
                key_bindings=bindings,
                style=style,
                bottom_toolbar=toolbar,
                multiline=True,
                reserve_space_for_menu=5,
                mouse_support=False,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "console" in message or "terminal" in message:
                self._plain_input = True
                return None
            raise
        return self._session

    def ask(self, question: str) -> str:
        self._stop_spinner()
        try:
            return input(question)
        except (EOFError, KeyboardInterrupt):
            return ""

    # -- turn events -------------------------------------------------------
    def turn_start(self) -> None:
        self.println()

    def request_start(self, label: str = "thinking") -> None:
        self._stop_spinner()
        self._spinner = Spinner(label, enabled=self.use_color)
        self._spinner.start()

    def text(self, delta: str) -> None:
        self._stop_spinner()
        if self._stream is None:
            self._stream = MarkdownStream(self._write, enabled=self.use_color)
        self._stream.feed(delta)

    def request_finish(self) -> None:
        self._stop_spinner()
        if self._stream is not None:
            self._stream.flush()
            self._stream = None

    def tool_start(self, name: str, summary: str) -> None:
        self.request_finish()
        label = f"{name} {summary}".rstrip()
        self._stop_spinner()
        self._spinner = Spinner(label, enabled=self.use_color)
        self._spinner.start()

    def tool_end(self, name, summary, result, elapsed, ok) -> None:
        self._stop_spinner()
        dot = self._paint("●", FG_GREEN if ok else FG_RED)
        label = self._paint(sanitize(f"{name} {summary}".rstrip()), FG_WHITE)
        time_text = self._paint(duration(elapsed), FG_DIM)
        self._write(f"  {dot} {label} {self._paint('·', FG_FAINT)} {time_text}")

        if not ok:
            headline = sanitize(result_headline(result, 80))
            self._write(" " + self._paint("· " + headline, FG_RED) + "\n")
            return

        if self.verbose:
            self._write("\n")
            for line in result.splitlines()[:60]:
                self._write(f"    {self._paint(sanitize(line), FG_DIM)}\n")
            return

        if name in ("pwsh", "search"):
            headline = sanitize(result_headline(result, max(30, terminal_width() - 24)))
            self._write(" " + self._paint("· " + headline, FG_DIM))
        self._write("\n")

    def turn_end(
        self, elapsed, usage, context_percent: int, steps: int, ok: bool = True
    ) -> None:
        self.request_finish()
        self.context_percent = context_percent
        mark = self._paint("✓", FG_GREEN) if ok else self._paint("!", FG_YELLOW)
        parts = [mark, self._paint(duration(elapsed), FG_DIM)]
        if usage is not None and (usage.prompt_tokens or usage.completion_tokens):
            from .tokens import format_int

            parts.append(
                self._paint(
                    f"{format_int(usage.prompt_tokens)}↑ {format_int(usage.completion_tokens)}↓",
                    FG_DIM,
                )
            )
        parts.append(self._paint(f"ctx {context_percent}%", FG_DIM))
        if steps > 1:
            parts.append(self._paint(f"{steps} steps", FG_DIM))
        self.println("\n  " + "  ·  ".join(parts))

    def info(self, message: str) -> None:
        self.println("  " + self._paint(sanitize(message), FG_DIM))

    def warn(self, message: str) -> None:
        self.println(
            "  " + self._paint("! ", FG_YELLOW) + self._paint(sanitize(message), FG_YELLOW)
        )

    def error(self, message: str) -> None:
        self.println("  " + self._paint("error ", FG_RED) + sanitize(message))

    def set_context(self, percent: int, tokens: int) -> None:
        self.context_percent = percent
        self.context_tokens = tokens

    def set_model(self, model: str) -> None:
        self.model = model

    def set_permission(self, mode: str) -> None:
        self.permission_label = mode

    def set_effort(self, effort: str, ultracode: bool) -> None:
        self.effort_level = effort
        self.ultracode = ultracode

    def set_logged_in(self, logged_in: bool) -> None:
        self.logged_in = bool(logged_in)

    def approve(self, tool: str, summary: str, detail: str) -> bool:
        self._stop_spinner()
        self.println()
        self.println("  " + self._paint(f"permission: {tool} {summary}", FG_YELLOW))
        for line in str(detail).splitlines()[:10]:
            self.println("    " + self._paint(sanitize(line), FG_DIM))
        answer = self.ask("Allow? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    def ask_user(self, question: str, options: list[str]) -> str:
        self._stop_spinner()
        self.println()
        self.println("  " + self._paint(sanitize(question), FG_WHITE, BOLD))
        for index, option in enumerate(options, 1):
            self.println(f"   {index}. {sanitize(option)}")
        self.println("   o. Other (type your answer)")
        answer = self.ask("Select [1-N/o] or type custom: ").strip()
        if not answer:
            return "User cancelled the question."
        if answer.lower() in {"o", "other"}:
            custom = self.ask("Your answer: ").strip()
            return custom or "User cancelled the question."
        try:
            selected = int(answer)
        except ValueError:
            return answer
        if 1 <= selected <= len(options):
            return options[selected - 1]
        return answer
