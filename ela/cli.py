"""Command-line entry point for ela."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .agent import Agent, friendly_error
from .budget import breakdown
from .goal import run_goal
from .config import (
    Config,
    ConfigError,
    apply_environment,
    display_path,
    ensure_home,
    has_credentials,
    init_config,
    load_config,
    save_config,
)
from .permissions import PermissionManager, PermissionMode, parse_mode
from .providers import PROVIDERS
from .retry import RetryPolicy, retry_call
from .session import (
    NullSession,
    Session,
    SessionInfo,
    latest_session,
    list_sessions,
    open_session,
    resume_by_id,
)
from .tokens import format_int
from .tools import execute_tool, one_line
from .ui import COMMANDS, PlainUI, TerminalUI, UI, enable_ansi, sanitize

HELP_EPILOG = """\
examples:
  ela                              full-screen interactive TUI
  ela --inline                     scrolling REPL
  ela "fix the failing tests"      one-shot
  ela -y "fix the failing tests"   one-shot with Auto Mode
  ela --continue                   resume the latest session here
  ela --resume                     pick a saved session
  ela --tokens                     show the prompt budgets

model configuration (env wins over ~/.ela/config.json):
  OPENAI_API_KEY / ELA_API_KEY
  OPENAI_BASE_URL / ELA_BASE_URL
  ELA_MODEL
"""


class CaptureUI(UI):
    """Silent UI for --json; records errors for the JSON payload."""

    def __init__(self):
        self.errors: list[str] = []
        self.verbose = False

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.errors.append(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ela",
        description="oh-my-elagent — a minimal, fast terminal coding agent.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="*", help="one-shot prompt; omit for interactive")
    parser.add_argument("-m", "--model", help="model id (overrides env/config)")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("-c", "--continue", dest="continue_", action="store_true",
                        help="continue the latest session in this directory")
    parser.add_argument("-r", "--resume", nargs="?", const="", default=None,
                        metavar="ID", help="resume a session (prompts if ID omitted)")
    parser.add_argument("--no-save", action="store_true", help="do not write a session file")
    parser.add_argument("--json", action="store_true", help="one-shot JSON output")
    parser.add_argument("--inline", action="store_true", help="use the scrolling REPL instead of the full-screen TUI")
    parser.add_argument(
        "--permission",
        choices=["auto", "ask", "readonly", "autoreview"],
        help="permission mode (default: ask)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="alias for --permission auto",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show full tool output")
    parser.add_argument("--tokens", action="store_true", help="print prompt budget and exit")
    parser.add_argument("--init", action="store_true", help="write the default config and exit")
    parser.add_argument("--version", action="version", version=f"ela {__version__}")
    return parser


def print_budget(cwd: Path) -> int:
    data = breakdown(cwd)
    rows = [
        ("base system", data["base_system"]),
        ("base tools", data["base_tools"]),
        ("base total", data["base"]),
        ("base budget", data["base_budget"]),
        ("base headroom", data["base_left"]),
        ("max system", data["max_system"]),
        ("max tools", data["max_tools"]),
        ("max total", data["max"]),
        ("max budget", data["max_budget"]),
        ("max headroom", data["max_left"]),
    ]
    for label, value in rows:
        print(f"  {label:<15} {value:>5}")
    if data["base_left"] >= 0 and data["max_left"] >= 0:
        return 0
    for name in ("base", "max"):
        if data[f"{name}_left"] < 0:
            print(f"  {name} over budget by {-data[f'{name}_left']} tokens")
    return 1


def report_config_error(args: argparse.Namespace, config: Config, exc: Exception) -> None:
    if args.json:
        print(
            json.dumps(
                {
                    "text": "",
                    "model": config.model,
                    "session": None,
                    "failed": True,
                    "errors": [str(exc)],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                },
                ensure_ascii=False,
            )
        )
        return
    print(f"ela: {exc}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  export OPENAI_API_KEY=sk-...", file=sys.stderr)
    print("  export ELA_MODEL=gpt-4.1-mini", file=sys.stderr)
    print("  # or: ela --init   (then edit ~/.ela/config.json)", file=sys.stderr)


MODEL_DISCOVERY_TIMEOUT = 20.0


def make_login_callback(agent: Agent, ui: UI, config: Config):
    """Validate and probe an endpoint, then persist it only on success.

    Runs a real request, so the UI must call this off its event loop. Returns
    ``(models, error)``; on failure the previous credentials are restored.
    """

    def callback(base_url: str, api_key: str) -> tuple[list[str], str | None]:
        base_url = (base_url or "").strip().rstrip("/")
        api_key = (api_key or "").strip() or "not-needed"
        if not base_url.startswith(("http://", "https://")):
            return [], "Base URL must start with http:// or https://"
        saved_url = config.base_url
        saved_key = config.api_key
        saved_client = agent.client
        try:
            agent.set_credentials(base_url, api_key)
            models = fetch_models(agent)
        except Exception as exc:
            config.base_url = saved_url
            config.api_key = saved_key
            agent.client = saved_client
            return [], f"login failed: {friendly_error(exc)}"
        try:
            save_config(config)
        except Exception as exc:
            config.base_url = saved_url
            config.api_key = saved_key
            agent.client = saved_client
            return [], f"could not save config: {exc}"
        ui.set_logged_in(True)
        ui.info(f"logged in · {base_url}")
        return models, None

    return callback


def fetch_models(
    agent: Agent, timeout: float = MODEL_DISCOVERY_TIMEOUT
) -> list[str]:
    client = agent.client
    if timeout is not None:
        # The SDK's own retries stay off here: discovery must stay snappy, so we
        # do at most one cheap extra attempt instead of the full backoff ladder.
        client = client.with_options(timeout=timeout, max_retries=0)
    policy = RetryPolicy(max_attempts=2, base_delay=0.5, max_delay=2.0)
    models = retry_call(lambda: client.models.list(), policy=policy)
    return sorted({model.id for model in models})


def restore_session_model(agent: Agent, session, ui: UI) -> None:
    if getattr(agent, "explicit_model", False):
        return
    model = getattr(session, "model", "") or ""
    if model and model != agent.config.model:
        agent.set_model(model)
        ui.info(f"model → {model} (from session)")


def choose_model_inline(
    ui: UI, agent: Agent, models: list[str], config: Config | None = None
) -> None:
    if not models:
        ui.warn("the endpoint returned no models")
        return
    ui.info(f"current model: {agent.config.model}")
    for index, model in enumerate(models[:50], 1):
        ui.info(f"  {index:>2}. {model}")
    answer = ui.ask("select # (Enter cancels): ").strip()
    if not answer:
        return
    try:
        selected = int(answer)
    except ValueError:
        ui.warn(f"not a number: {answer}")
        return
    if not 1 <= selected <= min(50, len(models)):
        ui.warn("selection out of range")
        return
    chosen = models[selected - 1]
    agent.set_model(chosen)
    note = "session only"
    if config is not None:
        save_config(config)
        note = "saved"
    ui.info(f"model → {chosen} ({note})")


def inline_login(agent: Agent, ui: UI, config: Config, callback) -> None:
    ui.info("select an endpoint:")
    for index, provider in enumerate(PROVIDERS, 1):
        url = provider.base_url or "(custom)"
        note = "" if provider.needs_key else "  · no key"
        ui.info(f"  {index:>2}. {provider.label:<22} {url}{note}")
    answer = ui.ask("provider # (Enter cancels): ").strip()
    if not answer:
        return
    try:
        provider = PROVIDERS[int(answer) - 1]
    except (ValueError, IndexError):
        ui.warn("invalid selection")
        return
    base_url = provider.base_url
    if not base_url:
        base_url = ui.ask("Base URL: ").strip()
        if not base_url:
            return
    label = "API key: " if provider.needs_key else "API key (optional): "
    key = getpass.getpass(label).strip()
    if provider.needs_key and not key:
        ui.error("API key is required for this provider")
        return
    ui.request_start("connecting")
    models, error = callback(base_url, key)
    ui.request_finish()
    if error:
        ui.error(error)
        return
    choose_model_inline(ui, agent, models, config)


def _when(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return (value or "")[:16]


def pick_session(ui: UI, cwd: Path) -> SessionInfo | None:
    infos = list_sessions(cwd)
    if not infos:
        ui.info("no saved sessions in this project")
        return None
    ui.info("saved sessions:")
    for index, info in enumerate(infos, 1):
        snippet = info.snippet or "(no prompt)"
        ui.info(f"  {index:>2}. {_when(info.created)} · {info.messages:>3} msgs · {snippet}")
    answer = ui.ask("resume # (Enter cancels): ").strip()
    if not answer:
        return None
    try:
        selected = int(answer)
    except ValueError:
        ui.warn(f"not a number: {answer}")
        return None
    if not 1 <= selected <= len(infos):
        ui.warn("selection out of range")
        return None
    return infos[selected - 1]


def handle_command(
    line: str, agent: Agent, ui: UI, config: Config, cwd: Path
) -> bool:
    """Handle a slash command. Returns False to exit the REPL."""
    parts = line.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command in ("/exit", "/quit", "/q"):
        return False

    if command == "/help":
        ui.info("commands:")
        for name, description in COMMANDS:
            ui.info(f"  {name:<10} {description}")
        ui.info("  !cmd       run a pwsh command and add output to context")
        ui.info("  !!cmd      run a pwsh command without adding context")
        return True

    if command == "/login":
        callback = make_login_callback(agent, ui, config)

        def choose_and_save(model: str) -> None:
            agent.set_model(model)
            save_config(config)

        if getattr(ui, "supports_login_modal", False):
            ui.open_login(
                callback,
                choose_and_save,
                config.base_url or "",
                agent.config.model,
            )
        else:
            inline_login(agent, ui, config, callback)
        return True

    if command == "/model":
        if arg:
            agent.set_model(arg)
            ui.info(f"model → {arg} (session only)")
            return True
        if not has_credentials(config):
            ui.warn("not logged in; use /login first")
            return True
        ui.request_start("loading models")
        try:
            models = fetch_models(agent)
        except Exception as exc:
            ui.request_finish()
            ui.error(f"could not list models: {friendly_error(exc)}")
            return True
        ui.request_finish()
        if not models:
            ui.warn("the endpoint returned no models")
            return True
        if getattr(ui, "supports_model_picker", False):
            ui.open_model_picker(models, agent.config.model, agent.set_model)
            return True

        choose_model_inline(ui, agent, models)
        return True

    if command == "/permissions":
        if not arg:
            ui.info(f"permissions: {agent.permissions.label}")
            ui.info("  /permissions auto|ask|readonly|autoreview|cycle")
            ui.info("  shift+tab cycles in the TUI; Auto Mode is unrestricted")
            return True
        if arg.lower() == "cycle":
            mode = agent.permissions.cycle()
        else:
            mode = parse_mode(arg)
            if mode is None:
                ui.warn("mode must be auto, ask, readonly, or autoreview")
                return True
            agent.permissions.set_mode(mode)
        ui.set_permission(agent.permissions.label)
        ui.info(f"permissions → {agent.permissions.label}")
        return True

    if command == "/effort":
        if getattr(ui, "supports_modal_effort", False) and not arg:
            def apply_effort(level: str, ultracode: bool) -> None:
                agent.set_effort(level)
                agent.set_ultracode(ultracode)
                ui.info(
                    f"effort → {level}"
                    + (" · ultracode" if ultracode else "")
                )

            ui.open_effort(agent.effort, agent.ultracode, apply_effort)
            return True
        if not arg:
            ui.info(
                f"effort: {agent.effort} · "
                f"ultracode: {'on' if agent.ultracode else 'off'}"
            )
            ui.info("  /effort low|medium|high")
            ui.info("  /effort ultracode on|off")
            return True
        parts = arg.split()
        if parts[0] in ("low", "medium", "high"):
            agent.set_effort(parts[0])
            ui.info(f"effort → {parts[0]}")
            return True
        if parts[0] in ("ultracode", "ultra"):
            value = parts[1].lower() if len(parts) > 1 else "on"
            enabled = value in ("on", "1", "true", "yes")
            agent.set_ultracode(enabled)
            ui.info(f"ultracode → {'on' if enabled else 'off'}")
            return True
        ui.warn("usage: /effort low|medium|high|ultracode on|off")
        return True

    if command in ("/maxstep", "/maxsteps"):
        if not arg:
            limit = max(0, int(agent.config.max_steps or 0))
            ui.info(
                "max steps: "
                + ("unlimited" if limit == 0 else str(limit))
            )
            ui.info("  /maxstep <n> sets a cap; /maxstep off removes it")
            return True
        if arg.lower() in ("0", "off", "unlimited", "none"):
            limit = 0
        else:
            try:
                limit = int(arg)
            except ValueError:
                ui.warn("usage: /maxstep <n>|off")
                return True
            if limit < 1:
                ui.warn("max steps must be positive, or 0/off for unlimited")
                return True
        config.max_steps = limit
        try:
            save_config(config)
        except Exception as exc:
            ui.error(f"could not save config: {exc}")
        ui.info(f"max steps → {'unlimited' if limit == 0 else limit} (saved)")
        return True

    if command == "/goal":
        if not arg:
            ui.info("usage: /goal <task> — plan and execute this goal once")
            return True
        if arg.lower() in ("on", "off"):
            ui.info("goal is per-command; use /goal <task>")
            return True
        run_goal(agent, ui, arg)
        return True

    if command in ("/clear", "/new"):
        if isinstance(agent.session, NullSession):
            session = NullSession(cwd)
        else:
            session = Session(cwd, model=config.model)
        agent.reset(session)
        ui.clear_transcript()
        ui.info(f"new session {session.id}")
        return True

    if command == "/compact":
        agent.compact()
        return True

    if command == "/session":
        ui.println()
        ui.info(f"session  {agent.session.id}")
        if agent.session.path is not None:
            ui.info(f"file     {display_path(agent.session.path)}")
        ui.info(
            f"messages {len(agent.messages) - 1} · ctx {agent.context_percent}% "
            f"({format_int(agent.context_tokens)} tok)"
        )
        usage = agent.total_usage
        ui.info(f"usage    {format_int(usage.prompt_tokens)}↑ {format_int(usage.completion_tokens)}↓")
        return True

    if command == "/resume":
        session = None
        if arg:
            session = resume_by_id(cwd, arg)
            if session is None:
                ui.warn(f"session not found: {arg}")
                return True
        elif getattr(ui, "supports_modal_ask", True):
            info = pick_session(ui, cwd)
            if info is None:
                return True
            session = open_session(info)
        else:
            infos = list_sessions(cwd)
            if not infos:
                ui.info("no saved sessions in this project")
                return True
            ui.info("saved sessions:")
            for info in infos:
                ui.info(
                    f"  {info.id}  {_when(info.created)} · "
                    f"{info.messages:>3} msgs · {info.snippet}"
                )
            ui.info("resume with: /resume <id>")
            return True

        messages = session.load()
        agent.adopt_session(session, messages)
        restore_session_model(agent, session, ui)
        ui.set_context(agent.context_percent, agent.context_tokens)
        ui.load_history(messages)
        ui.info(f"resumed {session.id} · {len(messages)} messages")
        return True

    if command == "/export":
        target = Path(arg).expanduser() if arg else cwd / f"ela-{agent.session.id}.md"
        if not target.is_absolute():
            target = cwd / target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(agent.export_markdown(), encoding="utf-8", newline="\n")
        except OSError as exc:
            ui.error(f"export failed: {exc}")
        else:
            ui.info(f"exported {target}")
        return True

    if command == "/verbose":
        ui.verbose = not ui.verbose
        config.verbose = ui.verbose
        ui.info(f"verbose {'on' if ui.verbose else 'off'}")
        return True

    if command == "/status":
        ui.println()
        ui.info(f"model     {config.model}")
        ui.info(f"endpoint  {config.base_url or 'https://api.openai.com/v1 (default)'}")
        ui.info(f"api key   {'set' if config.api_key else 'missing'}")
        ui.info(f"permission {agent.permissions.label}")
        ui.info(
            f"effort    {agent.effort} · ultracode "
            f"{'on' if agent.ultracode else 'off'}"
        )
        ui.info(f"context   {agent.context_percent}% ({format_int(agent.context_tokens)} / {format_int(config.context_window)})")
        ui.info(f"session   {agent.session.id}")
        data = breakdown(cwd)
        state = (
            "ok"
            if data["base_left"] >= 0 and data["max_left"] >= 0
            else "OVER"
        )
        ui.info(
            f"prompt    {data['base']} / {data['base_budget']} base · "
            f"{data['max']} / {data['max_budget']} max ({state})"
        )
        return True

    ui.warn(f"unknown command: {command}  (try /help)")
    return True


def shell_escape(line: str, agent: Agent, ui: UI, cwd: Path) -> None:
    inject = not line.startswith("!!")
    command = (line[2:] if not inject else line[1:]).strip()
    if not command:
        return
    label = one_line(command, 60)
    ui.turn_start()
    ui.request_start(f"pwsh {label}")
    started = time.perf_counter()
    result = execute_tool("pwsh", {"command": command}, cwd)
    ui.request_finish()
    elapsed = time.perf_counter() - started
    if result:
        ui.println()
        lines = result.splitlines()
        for text in lines[:60]:
            ui.println("  " + sanitize(text))
        if len(lines) > 60:
            ui.info(f"... {len(lines) - 60} more lines")
    note = "added to context" if inject else "not added to context"
    ui.info(f"$ {label} · {elapsed:.2f}s · {note}")
    if inject:
        message = {"role": "user", "content": f"[terminal] $ {command}\n{result}"}
        agent.messages.append(message)
        agent.session.append(message)
        ui.set_context(agent.context_percent, agent.context_tokens)


def dispatch_text(text: str, agent: Agent, ui: UI, config: Config, cwd: Path) -> bool:
    if text.startswith("/"):
        return handle_command(text, agent, ui, config, cwd)
    if text.startswith("!"):
        shell_escape(text, agent, ui, cwd)
        return True
    agent.run(text)
    return True


def interactive(
    agent: Agent,
    ui: TerminalUI,
    config: Config,
    cwd: Path,
    resumed: bool,
    notice: str | None = None,
) -> int:
    if not getattr(ui, "_banner_shown", False):
        ui.banner()
    if resumed:
        ui.info(f"resumed {agent.session.id} · {len(agent.messages) - 1} messages")
    if notice:
        ui.info(notice)
    if config.verbose:
        ui.info("verbose on")

    while True:
        try:
            line = ui.prompt()
        except KeyboardInterrupt:
            ui.println()
            continue
        except EOFError:
            ui.println()
            break

        text = line.strip()
        if not text:
            continue
        if text in ("exit", "quit"):
            break
        try:
            if not dispatch_text(text, agent, ui, config, cwd):
                break
        except KeyboardInterrupt:
            ui.warn("interrupted")
        except Exception as exc:
            ui.error(friendly_error(exc))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    enable_ansi()

    cwd = Path.cwd().resolve()

    if args.tokens:
        return print_budget(cwd)
    if args.init:
        path = init_config()
        print(f"wrote {path}")
        print("Set OPENAI_API_KEY, then run: ela")
        return 0

    ensure_home()
    prompt_text = " ".join(args.prompt).strip()
    interactive_mode = not prompt_text
    if args.json and interactive_mode:
        parser.error("--json requires a one-shot prompt")
    if args.json and args.resume == "":
        parser.error("--json requires an explicit session ID with --resume")

    config = apply_environment(load_config())
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.verbose:
        config.verbose = True
    permission_value = args.permission or ("full" if args.yes else config.permission)
    permission_mode = parse_mode(permission_value) or PermissionMode.ASK
    permissions = PermissionManager(cwd, permission_mode)
    logged_in = has_credentials(config)

    # Resolve the session first so the full-screen UI can render its history.
    session: Session | NullSession | None = None
    messages = None
    resumed = False
    startup_notice = None
    if args.resume is not None:
        if args.resume:
            found = resume_by_id(cwd, args.resume)
            if found is None:
                message = f"session not found: {args.resume}"
                if args.json:
                    print(
                        json.dumps(
                            {
                                "text": "",
                                "model": config.model,
                                "session": args.resume,
                                "failed": True,
                                "errors": [message],
                                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                            },
                            ensure_ascii=False,
                        )
                    )
                else:
                    print(f"ela: {message}", file=sys.stderr)
                return 2
            session = found
        else:
            picker = PlainUI(verbose=True)
            info = pick_session(picker, cwd)
            if info is None:
                return 0
            session = open_session(info)
        messages = session.load()
        resumed = True
    elif args.continue_:
        info = latest_session(cwd)
        if info is None:
            startup_notice = "no saved session here; starting fresh"
        else:
            session = open_session(info)
            messages = session.load()
            resumed = True

    if session is None:
        if args.no_save:
            session = NullSession(cwd)
        else:
            session = Session(cwd, model=config.model)

    use_tui = False
    inline_env = os.environ.get("ELA_INLINE", "").lower() in {"1", "true", "yes"}
    if interactive_mode:
        want_tui = (
            not args.inline
            and not inline_env
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )
        if want_tui:
            try:
                from .fullscreen import FullScreenUI

                ui = FullScreenUI(
                    cwd,
                    config.model,
                    verbose=config.verbose,
                    context_window=config.context_window,
                    messages=messages,
                    notice=startup_notice,
                    logged_in=logged_in,
                )
                use_tui = True
            except Exception:
                ui = TerminalUI(cwd, config.model, verbose=config.verbose)
                ui.set_logged_in(logged_in)
        else:
            ui = TerminalUI(cwd, config.model, verbose=config.verbose)
            ui.set_logged_in(logged_in)
    elif args.json:
        ui = CaptureUI()
    else:
        ui = PlainUI(verbose=config.verbose)

    try:
        agent = Agent(
            config,
            cwd,
            ui,
            session,
            messages=messages,
            permissions=permissions,
        )
    except ConfigError as exc:
        report_config_error(args, config, exc)
        return 2

    agent.explicit_model = bool(args.model or os.environ.get("ELA_MODEL"))
    if resumed and session is not None:
        restore_session_model(agent, session, ui)

    ui.set_permission(permissions.label)
    ui.set_effort(agent.effort, agent.ultracode)
    if isinstance(ui, TerminalUI):
        def cycle_permission() -> None:
            permissions.cycle()
            ui.set_permission(permissions.label)

        ui.on_permission_cycle = cycle_permission

    if use_tui:
        tui = ui
        tui.context_percent = agent.context_percent
        tui.context_tokens = agent.context_tokens
        tui.set_agent(agent)

        def handler(text: str) -> bool:
            return dispatch_text(text, agent, tui, config, cwd)

        tui.on_submit = handler
        try:
            return tui.run()
        except Exception as exc:
            message = str(exc).lower()
            if not any(key in message for key in ("console", "terminal", "tty")):
                raise
            fallback = TerminalUI(cwd, config.model, verbose=config.verbose)
            fallback.set_permission(permissions.label)
            fallback.set_effort(agent.effort, agent.ultracode)

            def cycle_fallback_permission() -> None:
                permissions.cycle()
                fallback.set_permission(permissions.label)

            fallback.on_permission_cycle = cycle_fallback_permission
            agent.ui = fallback
            return interactive(
                agent, fallback, config, cwd, resumed, notice=startup_notice
            )

    if interactive_mode:
        return interactive(agent, ui, config, cwd, resumed, notice=startup_notice)

    agent.run(prompt_text)
    if args.json:
        usage = agent.last_usage
        payload = {
            "text": agent.last_response,
            "model": config.model,
            "session": agent.session.id,
            "failed": agent.last_failed,
            "errors": getattr(ui, "errors", []),
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
        }
        print(json.dumps(payload, ensure_ascii=False))
    return 1 if agent.last_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
