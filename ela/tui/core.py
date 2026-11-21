"""The full-screen UI shell: event queue, lifecycle, and the UI interface."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..ui import UI, sanitize
from .askuser import AskUserMixin
from .goal import GoalMixin
from .layout import LayoutMixin
from .login import LoginMixin
from .modals import EffortPermissionMixin, PermissionRequest
from .transcript import TranscriptMixin, TranscriptModel


class FullScreenUI(
    LayoutMixin,
    TranscriptMixin,
    LoginMixin,
    AskUserMixin,
    EffortPermissionMixin,
    GoalMixin,
    UI,
):
    """Full-screen terminal UI: alternate screen, streaming transcript, modals."""

    supports_modal_ask = False
    supports_modal_effort = True
    supports_permission_modal = True
    supports_login_modal = True
    supports_model_picker = True

    def __init__(
        self,
        cwd: Path,
        model: str,
        verbose: bool = False,
        context_window: int = 128_000,
        messages: list[dict[str, Any]] | None = None,
        notice: str | None = None,
        logged_in: bool = True,
        input=None,
        output=None,
    ):
        self.cwd = cwd
        self.model = model
        self.verbose = verbose
        self.context_window = max(1, int(context_window))
        self.context_percent = 0
        self.context_tokens = 0
        self.busy = False
        self.status_label = "ready"
        self.follow = True
        self.spin_frame = 0
        self.model_state = TranscriptModel()
        self.permission_label = "ask"
        self.effort_level = "medium"
        self.ultracode = False
        self.logged_in = bool(logged_in)
        self.goal_total = 0
        self.goal_done = 0
        self.goal_status = ""
        self.effort_open = False
        self.effort_callback = None
        self.effort_draft_level = "medium"
        self.effort_draft_ultracode = False
        self.permission_open = False
        self.permission_current: PermissionRequest | None = None
        self.permission_queue: list[PermissionRequest] = []
        self.permission_lock = threading.Lock()
        self.login_open = False
        self.login_stage = "provider"
        self.login_callback = None
        self.login_connecting = False
        self.login_on_model = None
        self.login_current_model = ""
        self.login_provider = None
        self.login_error = ""
        self.provider_index = 0
        self.provider_offset = 0
        self.askuser_open = False
        self.askuser_question = ""
        self.askuser_options: list[str] = []
        self.askuser_index = 0
        self.askuser_other = False
        self.askuser_event: threading.Event | None = None
        self.askuser_result: dict[str, str] | None = None
        self.model_picker_open = False
        self.model_picker_models: list[str] = []
        self.model_picker_index = 0
        self.model_picker_offset = 0
        self.model_picker_current = ""
        self.model_picker_callback = None
        self.model_picker_note = "session only"

        self._events: list[tuple[str, tuple[Any, ...]]] = []
        self._event_lock = threading.Lock()
        self._drain_scheduled = False
        self._app_running = False
        self._spin_task = None
        self._agent = None
        self.on_submit: Callable[[str], bool] | None = None
        self._input = input
        self._output = output

        self._build_layout()
        self._clear_direct()
        if messages:
            self._render_history_direct(messages)
        elif self.logged_in:
            self._append_info(f"ela {__version__} · ready. /help for commands.")
            self._append_blank()
        else:
            self._append_info(
                "Not logged in. Use /login to configure the endpoint and API key."
            )
            self._append_info("Then use /model to choose an available model.")
            self._append_blank()
        if notice:
            self._append_info(notice)
            self._append_blank()

    def set_agent(self, agent) -> None:
        self._agent = agent

    def run(self) -> int:
        self._app_running = True
        try:
            # Apply events posted before the loop started (startup notices,
            # a model restored from the resumed session, ...).
            self.app.run(pre_run=self._drain)
        except KeyboardInterrupt:
            pass
        finally:
            self._app_running = False
            if self._agent is not None:
                self._agent.cancel()
        return 0

    def _submit_text(self, text: str) -> None:
        self._append_user(text)
        self.busy = True
        self.status_label = "starting"
        self.follow = True
        self._scroll_to_end()
        self._start_spinner()
        if self.on_submit is None:
            self._post("error", "no submit handler configured")
            self._post("task_done", True)
            return
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _worker(self, text: str) -> None:
        keep_going = True
        try:
            keep_going = bool(self.on_submit(text))
        except Exception as exc:
            self._post("error", f"{type(exc).__name__}: {exc}")
        finally:
            self._post("task_done", keep_going)

    def _post(self, name: str, *args) -> None:
        with self._event_lock:
            self._events.append((name, args))
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        try:
            self.app.loop.call_soon_threadsafe(self._drain)
        except Exception:
            with self._event_lock:
                self._drain_scheduled = False

    def _drain(self) -> None:
        if not self._app_running:
            return
        with self._event_lock:
            events, self._events = self._events, []
            self._drain_scheduled = False
        for name, args in events:
            try:
                self._apply(name, args)
            except Exception as exc:
                try:
                    self._append_error(f"ui: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        self.app.invalidate()

    def _apply(self, name: str, args: tuple[Any, ...]) -> None:
        if name == "turn_start":
            self._append_blank()
        elif name == "text":
            self._on_text(args[0])
        elif name == "request_start":
            self.status_label = args[0]
        elif name == "request_finish":
            self._on_request_finish()
        elif name == "tool_start":
            self.status_label = sanitize(f"{args[0]} {args[1]}".strip())
        elif name == "tool_end":
            self._on_tool_end(*args)
        elif name == "turn_end":
            self._on_turn_end(*args)
        elif name == "info":
            self._append_info(args[0])
        elif name == "warn":
            self._append_warn(args[0])
        elif name == "error":
            self._append_error(args[0])
        elif name == "println":
            self._append_plain(args[0])
        elif name == "context":
            self.context_percent, self.context_tokens = args
        elif name == "model":
            self.model = args[0]
        elif name == "permission":
            self.permission_label = args[0]
        elif name == "effort":
            self.effort_level = args[0]
            self.ultracode = bool(args[1])
        elif name == "effort_open":
            self._open_effort(*args)
        elif name == "permission_show":
            self._show_next_permission()
        elif name == "logged_in":
            self.logged_in = bool(args[0])
        elif name == "login_open":
            self._open_login(*args)
        elif name == "login_result":
            self._on_login_result(*args)
        elif name == "model_picker_open":
            self._open_model_picker(*args)
        elif name == "askuser_open":
            self._open_askuser(*args)
        elif name == "goal_start":
            self._goal_start_direct(args[0])
        elif name == "goal_plan":
            self._goal_plan_direct(args[0], args[1])
        elif name == "goal_task_start":
            self._goal_task_start_direct(*args)
        elif name == "goal_task_end":
            self._goal_task_end_direct(*args)
        elif name == "goal_finish":
            self._goal_finish_direct(*args)
        elif name == "clear":
            self._clear_direct()
        elif name == "history":
            self._render_history_direct(args[0])
        elif name == "task_done":
            self._finish_task(args[0])

    def turn_start(self) -> None:
        self._post("turn_start")

    def request_start(self, label: str = "thinking") -> None:
        self._post("request_start", label)

    def text(self, delta: str) -> None:
        self._post("text", delta)

    def request_finish(self) -> None:
        self._post("request_finish")

    def tool_start(self, name: str, summary: str) -> None:
        self._post("tool_start", name, summary)

    def tool_end(self, name, summary, result, elapsed, ok) -> None:
        self._post("tool_end", name, summary, result, elapsed, ok)

    def turn_end(self, elapsed, usage, context_percent: int, steps: int, ok: bool = True) -> None:
        self._post("turn_end", elapsed, usage, context_percent, steps, ok)

    def info(self, message: str) -> None:
        self._post("info", message)

    def warn(self, message: str) -> None:
        self._post("warn", message)

    def error(self, message: str) -> None:
        self._post("error", message)

    def println(self, text: str = "") -> None:
        self._post("println", text)

    def set_context(self, percent: int, tokens: int) -> None:
        self._post("context", percent, tokens)

    def set_model(self, model: str) -> None:
        if self._app_running:
            self._post("model", model)
        else:
            self.model = model

    def clear_transcript(self) -> None:
        self._post("clear")

    def load_history(self, messages) -> None:
        self._post("history", list(messages))

    def set_permission(self, mode: str) -> None:
        if self._app_running:
            self._post("permission", mode)
        else:
            self.permission_label = mode

    def set_effort(self, effort: str, ultracode: bool) -> None:
        if self._app_running:
            self._post("effort", effort, ultracode)
        else:
            self.effort_level = effort
            self.ultracode = bool(ultracode)

    def set_logged_in(self, logged_in: bool) -> None:
        if self._app_running:
            self._post("logged_in", logged_in)
        else:
            self.logged_in = bool(logged_in)

    def open_login(
        self,
        on_submit,
        on_model,
        base_url: str = "",
        current_model: str = "",
    ) -> None:
        self._post(
            "login_open",
            on_submit,
            on_model,
            base_url,
            current_model,
        )

    def open_model_picker(
        self,
        models,
        current: str,
        on_select,
        note: str = "session only",
    ) -> None:
        self._post("model_picker_open", list(models), current, on_select, note)

    def ask_user(self, question: str, options: list[str]) -> str:
        if not self._app_running:
            return "No user available to answer."
        event = threading.Event()
        result = {"value": "User did not answer."}
        self._post("askuser_open", question, list(options), event, result)
        if not event.wait(600):
            return "User did not answer."
        return result.get("value") or "User did not answer."

    def goal_start(self, goal: str) -> None:
        self._post("goal_start", goal)

    def goal_plan(self, goal: str, tasks) -> None:
        self._post("goal_plan", goal, list(tasks))

    def goal_task_start(self, index: int, total: int, title: str) -> None:
        self._post("goal_task_start", index, total, title)

    def goal_task_end(self, index, total, title, ok, elapsed, summary) -> None:
        self._post(
            "goal_task_end", index, total, title, ok, elapsed, summary
        )

    def goal_finish(self, ok: bool, completed: int, total: int) -> None:
        self._post("goal_finish", ok, completed, total)

    def open_effort(self, effort: str, ultracode: bool, on_apply) -> None:
        self._post("effort_open", effort, ultracode, on_apply)

    def approve(self, tool: str, summary: str, detail: str) -> bool:
        request = PermissionRequest(
            sanitize(str(tool)),
            sanitize(str(summary))[:200],
            sanitize(str(detail))[:1200],
        )
        with self.permission_lock:
            self.permission_queue.append(request)
        self._post("permission_show")
        if not request.event.wait(300):
            return False
        return request.allowed
