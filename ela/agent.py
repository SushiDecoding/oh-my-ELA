"""The agent loop: stream a completion, run tools, repeat."""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import Config
from .permissions import PermissionManager, PermissionMode, parse_mode
from .prompt import (
    build_subagent_prompt,
    build_system_prompt,
    get_tools,
    read_project_context,
)
from .retry import retry_call
from .session import NullSession, Session
from .tokens import estimate_tokens, format_int, trim_messages
from .tools import execute_tool, result_headline, summarize_call
from .ui import UI

MAX_SUBAGENT_DEPTH = 2


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def create_client(config: Config):
    from openai import OpenAI

    return OpenAI(
        api_key=config.api_key or "not-needed",
        base_url=config.base_url or None,
        timeout=config.timeout,
        max_retries=2,
    )


def _int_setting(value: object, default: int) -> int:
    """Coerce a hand-edited config value without ever raising."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def friendly_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    message = " ".join(str(message).split())
    if len(message) > 500:
        message = message[:500] + "…"
    hints = {
        401: "authentication failed. Check OPENAI_API_KEY / ELA_API_KEY or run /login.",
        403: "access denied. Check the key's permissions.",
        404: "model or endpoint not found. Check ELA_MODEL and the base URL.",
        429: "rate limited. Retry shortly or switch models.",
    }
    hint = hints.get(status) if isinstance(status, int) else None
    if hint:
        return f"{hint} ({message})"
    if status:
        return f"API error {status}: {message}"
    if exc.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}:
        return f"connection failed: {message}"
    return f"{type(exc).__name__}: {message}"


class Agent:
    def __init__(
        self,
        config: Config,
        cwd: Path,
        ui: UI,
        session: Session,
        client=None,
        messages: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        permissions: PermissionManager | None = None,
        effort: str = "medium",
        ultracode: bool = False,
        external_cancel: threading.Event | None = None,
        subagent_depth: int = 0,
    ):
        self.config = config
        self.cwd = cwd
        self.ui = ui
        self.session = session
        self.client = client or create_client(config)
        self.permissions = permissions or PermissionManager(
            cwd, parse_mode(config.permission) or PermissionMode.ASK
        )
        self.effort = effort if effort in ("low", "medium", "high") else "medium"
        self.ultracode = bool(ultracode)
        self.external_cancel = external_cancel
        self.subagent_depth = int(subagent_depth)
        self._cancel = threading.Event()
        self._supports_reasoning_effort = True
        self._custom_system_prompt = system_prompt
        self.tools = (
            list(tools)
            if tools is not None
            else get_tools(self.ultracode, self.permissions.mode.value)
        )
        self.system_prompt = system_prompt or self._build_system_prompt()
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        if messages:
            self.load_messages(messages)
        self.total_usage = Usage()
        self.last_response = ""
        self.last_failed = False
        self.last_usage: Usage | None = None
        self._trim_notified = False
        self._tool_tokens = self._count_tool_tokens()

    # -- public ------------------------------------------------------------
    @property
    def context_tokens(self) -> int:
        return estimate_tokens(self.messages) + self._tool_tokens

    @property
    def context_percent(self) -> int:
        window = max(1, _int_setting(self.config.context_window, 128_000))
        return min(100, round(self.context_tokens * 100 / window))

    def _build_system_prompt(self) -> str:
        if self._custom_system_prompt is not None:
            return self._custom_system_prompt
        return build_system_prompt(
            self.cwd,
            read_project_context(self.cwd),
            effort=self.effort,
            ultracode=self.ultracode,
            permission=self.permissions.mode.value,
        )

    def _count_tool_tokens(self) -> int:
        return max(1, len(json.dumps(self.tools, ensure_ascii=False)) // 4)

    def _sync_system_prompt(self) -> None:
        if self._custom_system_prompt is not None:
            return
        prompt = self._build_system_prompt()
        tools = get_tools(self.ultracode, self.permissions.mode.value)
        if prompt == self.system_prompt and tools == self.tools:
            return
        self.system_prompt = prompt
        self.tools = tools
        self._tool_tokens = self._count_tool_tokens()
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = prompt

    def load_messages(self, messages: list[dict[str, Any]]) -> None:
        if messages and messages[0].get("role") == "system":
            messages = messages[1:]
        self._sync_system_prompt()
        self.messages = [{"role": "system", "content": self.system_prompt}, *messages]
        self._repair_tool_pairs()

    def reset(self, session: Session) -> None:
        self.session = session
        self._sync_system_prompt()
        self.system_prompt = self._build_system_prompt()
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.total_usage = Usage()
        self._trim_notified = False
        self.ui.set_context(0, 0)

    def adopt_session(self, session: Session, messages: list[dict[str, Any]]) -> None:
        self.session = session
        self.load_messages(messages)
        self.total_usage = Usage()
        self._trim_notified = False

    def set_model(self, model: str) -> None:
        self.config.model = model
        update = getattr(self.session, "update_model", None)
        if update is not None:
            update(model)
        self.ui.set_model(model)

    def set_credentials(self, base_url: str, api_key: str) -> None:
        self.config.base_url = base_url or None
        self.config.api_key = api_key or None
        self.client = create_client(self.config)
        self._supports_reasoning_effort = True

    def set_effort(self, effort: str) -> None:
        if effort not in ("low", "medium", "high"):
            return
        self.effort = effort
        self.ui.set_effort(self.effort, self.ultracode)
        self._sync_system_prompt()

    def set_ultracode(self, enabled: bool) -> None:
        self.ultracode = bool(enabled)
        self.ui.set_effort(self.effort, self.ultracode)
        self._sync_system_prompt()

    def review_action(self, tool, args, summary, detail):
        from .review import review_action as run_review

        return run_review(
            self.client, self.config, self.cwd, tool, args, summary, detail
        )

    def cancel(self) -> None:
        self._cancel.set()

    def _cancelled(self) -> bool:
        return self._cancel.is_set() or (
            self.external_cancel is not None and self.external_cancel.is_set()
        )

    def cancelled(self) -> bool:
        return self._cancelled()

    def run_subagent(self, description: str, prompt: str) -> tuple[str, bool]:
        """Run a fresh focused subagent and return (answer, ok)."""
        child = self._make_subagent(description)
        answer = child.run(prompt)
        if not answer.strip():
            errors = getattr(child.ui, "errors", [])
            if errors:
                answer = "Subagent errors: " + "; ".join(errors[-3:])
            elif child.last_failed:
                answer = "Subagent failed before producing a final answer."
            else:
                answer = "(subagent returned no final answer)"
        return answer.strip(), not child.last_failed

    def run(self, user_text: str) -> str:
        self._cancel.clear()
        self._sync_system_prompt()
        message = {"role": "user", "content": user_text}
        self.messages.append(message)
        self.session.append(message)

        started = time.perf_counter()
        usage = Usage()
        steps = 0
        failed = False
        interrupted = False
        self.last_response = ""
        self.last_failed = False
        self.ui.turn_start()

        try:
            limit = max(0, _int_setting(self.config.max_steps, 0))
            step_iter = iter(range(1, limit + 1)) if limit else itertools.count(1)
            for step in step_iter:
                steps = step
                if self._cancelled():
                    raise KeyboardInterrupt
                self._sync_system_prompt()
                self._prepare_context()
                self.ui.request_start("thinking")
                assistant, step_usage = self._stream_completion()
                usage.add(step_usage)
                self.ui.request_finish()

                if assistant is None:
                    failed = True
                    self.ui.warn("empty response")
                    break
                self.messages.append(assistant)
                self.session.append(assistant)

                calls = assistant.get("tool_calls") or []
                if assistant.get("content"):
                    self.last_response = assistant["content"]
                if not calls:
                    break
                if self._cancelled():
                    raise KeyboardInterrupt
                self._execute_tool_calls(calls)
            else:
                self.ui.warn(f"stopped after {limit} steps")
        except KeyboardInterrupt:
            interrupted = True
            self._cancel.set()
            self.ui.request_finish()
            self.ui.warn("interrupted")
        except Exception as exc:
            failed = True
            self.ui.request_finish()
            if os.environ.get("ELA_DEBUG"):
                raise
            self.ui.error(friendly_error(exc))
        finally:
            self._repair_tool_pairs()
            self.last_failed = failed or interrupted
            self.last_usage = usage
            self.total_usage.add(usage)
            self.ui.set_context(self.context_percent, self.context_tokens)
            self.ui.turn_end(
                time.perf_counter() - started,
                usage,
                self.context_percent,
                steps,
                ok=not (failed or interrupted),
            )
        return self.last_response

    def compact(self) -> bool:
        """Replace history with a model-written summary."""
        if len(self.messages) <= 2:
            self.ui.info("nothing to compact")
            return False
        self._prepare_context()
        self._repair_tool_pairs()
        before = self.context_tokens
        request = {
            "role": "user",
            "content": (
                "Summarize this session for another assistant that will continue it. "
                "Preserve: the user's goals, decisions made, files touched, commands "
                "run and their outcomes, open problems, and next steps. Be terse."
            ),
        }
        self.ui.request_start("compacting")
        policy = self.config.retry_policy()
        try:
            response = retry_call(
                lambda: self.client.chat.completions.create(
                    model=self.config.model,
                    messages=[*self.messages, request],
                    stream=False,
                ),
                policy=policy,
                should_stop=self._cancelled,
                on_retry=lambda exc, attempt, delay: self.ui.warn(
                    f"retrying in {delay:.1f}s "
                    f"(attempt {attempt}/{policy.max_attempts}): {friendly_error(exc)}"
                ),
            )
            summary = response.choices[0].message.content or ""
        except KeyboardInterrupt:
            self.ui.warn("interrupted")
            return False
        except Exception as exc:
            self.ui.error(friendly_error(exc))
            return False
        finally:
            self.ui.request_finish()
        if not summary.strip():
            self.ui.warn("model returned an empty summary; history kept")
            return False

        original = self.messages
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": "[Context compacted from the earlier session]\n\n" + summary.strip(),
            },
        ]
        after = self.context_tokens
        if after >= before:
            # A summary of a short session can be longer than the session.
            self.messages = original
            self.ui.warn(
                "compaction would not reduce context "
                f"({format_int(before)} → {format_int(after)} tokens); history kept"
            )
            self.ui.set_context(self.context_percent, self.context_tokens)
            return False
        self.session.rewrite(self.messages)
        self.ui.set_context(self.context_percent, self.context_tokens)
        self.ui.info(f"compacted {format_int(before)} → {format_int(after)} tokens")
        return True

    def export_markdown(self) -> str:
        lines = [
            "# ela session",
            "",
            f"- id: `{self.session.id}`",
            f"- model: `{self.config.model}`",
            f"- cwd: `{self.cwd}`",
            "",
        ]
        pending_results: dict[str, str] = {}
        for message in self.messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                continue
            if role == "user":
                if str(content or "").startswith("[ela-internal]"):
                    continue
                lines += ["## User", "", str(content or ""), ""]
            elif role == "assistant":
                if content and not str(content).startswith("[ela-internal]"):
                    lines += ["## Assistant", "", str(content), ""]
                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    pending_results[call.get("id", "")] = fn.get("name", "?")
                    lines += [
                        f"### tool: `{fn.get('name', '?')}`",
                        "",
                        "```json",
                        fn.get("arguments", ""),
                        "```",
                        "",
                    ]
            elif role == "tool":
                name = pending_results.get(message.get("tool_call_id", ""), "tool")
                text = str(content or "")
                if len(text) > 3000:
                    text = text[:3000] + "\n…"
                lines += [
                    f"<details><summary>{name} output</summary>",
                    "",
                    "```",
                    text,
                    "```",
                    "",
                    "</details>",
                    "",
                ]
        return "\n".join(lines)

    # -- internals ---------------------------------------------------------
    def _repair_tool_pairs(self) -> None:
        """Ensure every assistant tool_call has a matching tool result.

        An interrupt between a tool call and its result would otherwise leave
        a transcript that the Chat Completions API rejects.
        """
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.get("role") == "user":
                return
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            answered = {
                item.get("tool_call_id")
                for item in self.messages[index + 1 :]
                if item.get("role") == "tool"
            }
            for call in message["tool_calls"]:
                call_id = call.get("id")
                if call_id not in answered:
                    result = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": "Error: interrupted before the tool returned.",
                    }
                    self.messages.append(result)
                    self.session.append(result)
            return

    def _prepare_context(self) -> None:
        window = max(1, _int_setting(self.config.context_window, 128_000))
        budget = max(2000, int(window * 0.78) - self._tool_tokens)
        trimmed, dropped = trim_messages(self.messages, budget)
        if dropped:
            self.messages = trimmed
            if not self._trim_notified:
                self.ui.info(f"context trimmed ({dropped} older turns)")
                self._trim_notified = True

    def _stream_completion(self):
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": self.messages,
            "tools": self.tools,
            "stream": True,
        }
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if self.config.max_tokens:
            kwargs["max_tokens"] = self.config.max_tokens
        if self.config.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if self._supports_reasoning_effort and self.effort in ("low", "medium", "high"):
            kwargs["reasoning_effort"] = self.effort

        prompt_estimate = self.context_tokens
        stream = None

        def create_stream():
            # Capability probing (dropping unsupported kwargs) happens inside
            # the attempt so a retry starts from the degraded kwargs, not the
            # original ones.
            for _ in range(3):
                try:
                    return self.client.chat.completions.create(**kwargs)
                except Exception as exc:
                    message = str(exc).lower()
                    if "reasoning_effort" in message and "reasoning_effort" in kwargs:
                        self._supports_reasoning_effort = False
                        kwargs.pop("reasoning_effort", None)
                        continue
                    if "stream_options" in message and "stream_options" in kwargs:
                        self.config.stream_usage = False
                        kwargs.pop("stream_options", None)
                        continue
                    raise
            raise RuntimeError("could not create a completion stream")

        policy = self.config.retry_policy()
        # Only opening the stream is retried: nothing has been consumed yet, so
        # a retry cannot duplicate output. A stream that dies mid-flight is not
        # retried here.
        stream = retry_call(
            create_stream,
            policy=policy,
            should_stop=self._cancelled,
            on_retry=lambda exc, attempt, delay: self.ui.warn(
                f"retrying in {delay:.1f}s "
                f"(attempt {attempt}/{policy.max_attempts}): {friendly_error(exc)}"
            ),
        )
        if stream is None:
            raise RuntimeError("could not create a completion stream")

        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish_reason = None

        for chunk in stream:
            if self._cancelled():
                try:
                    stream.close()
                except Exception:
                    pass
                raise KeyboardInterrupt
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage.prompt_tokens = getattr(chunk_usage, "prompt_tokens", None) or usage.prompt_tokens
                usage.completion_tokens = (
                    getattr(chunk_usage, "completion_tokens", None) or usage.completion_tokens
                )
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            text = getattr(delta, "content", None)
            if isinstance(text, str) and text:
                content.append(text)
                self.ui.text(text)
            for call in getattr(delta, "tool_calls", None) or []:
                index = getattr(call, "index", None)
                index = 0 if index is None else index
                entry = calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if getattr(call, "id", None):
                    entry["id"] = call.id
                function = getattr(call, "function", None)
                if function is None:
                    continue
                name = getattr(function, "name", None)
                if isinstance(name, str) and name:
                    current = entry["function"]["name"]
                    if not current:
                        entry["function"]["name"] = name
                    elif name != current and not current.endswith(name):
                        # Some compatible servers stream the name in fragments.
                        entry["function"]["name"] = current + name
                arguments = getattr(function, "arguments", None)
                if isinstance(arguments, str) and arguments:
                    entry["function"]["arguments"] += arguments

        text = "".join(content)
        tool_calls = [entry for _, entry in sorted(calls.items())]
        for index, call in enumerate(tool_calls, 1):
            if not call.get("id"):
                call["id"] = f"call_{index}"
            call.setdefault("type", "function")

        if not usage.prompt_tokens:
            usage.prompt_tokens = prompt_estimate
        if not usage.completion_tokens:
            extra = sum(len(call["function"]["arguments"]) for call in tool_calls)
            usage.completion_tokens = max(1, (len(text) + extra) // 4)

        if finish_reason == "length":
            self.ui.warn("response truncated (length)")

        if not text and not tool_calls:
            return None, usage

        assistant: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        return assistant, usage

    # -- tool execution ----------------------------------------------------
    def _parse_call(self, call: dict[str, Any]):
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw = function.get("arguments") or ""
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            return name, {}, f"invalid JSON arguments: {exc}"
        if not isinstance(args, dict):
            return name, {}, "arguments must be a JSON object"
        return name, args, None

    def _permission_detail(self, name: str, args: dict[str, Any]) -> str:
        if name == "pwsh":
            return "PS> " + str(args.get("command") or "")[:400]
        if name == "write":
            content = str(args.get("content") or "")
            return (
                f"path: {args.get('path')}\n"
                f"bytes: {len(content.encode('utf-8'))}\n"
                f"preview: {content[:200]}"
            )
        if name == "edit":
            old = str(args.get("old") or "")
            new = str(args.get("new") or "")
            return (
                f"path: {args.get('path')}\n"
                f"old ({len(old)} chars): {old[:200]}\n"
                f"new ({len(new)} chars): {new[:200]}"
            )
        if name == "task":
            return str(args.get("prompt") or "")[:300]
        return ""

    def _tool_message(self, call: dict[str, Any], content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call.get("id") or "call_0",
            "content": content,
        }

    def _result_ok(self, result: str) -> bool:
        ok = not result.lstrip().startswith("Error:")
        match = re.search(r"(?:^|\n)exit (\d+)\s*$", result)
        if match and match.group(1) != "0":
            ok = False
        return ok

    def _execute_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        results: list[dict[str, Any] | None] = [None] * len(calls)
        tasks: list[tuple[int, dict[str, Any]]] = []
        for index, call in enumerate(calls):
            if self._cancelled():
                # Leave the rest without results; _repair_tool_pairs fills them
                # with an interrupted marker so the transcript stays valid.
                break
            name = (call.get("function") or {}).get("name") or ""
            if name == "task" and self.ultracode:
                tasks.append((index, call))
            else:
                results[index] = self._execute_call(call)
        if tasks and not self._cancelled():
            if len(tasks) == 1:
                index, call = tasks[0]
                results[index] = self._execute_task_call(call)
            else:
                self._execute_parallel_tasks(tasks, results)
        for message in results:
            if message is None:
                continue
            self.messages.append(message)
            self.session.append(message)

    def _execute_call(self, call: dict[str, Any]) -> dict[str, Any]:
        name, args, parse_error = self._parse_call(call)
        summary = summarize_call(name, args) if not parse_error else name
        if parse_error:
            self.ui.tool_start(name or "tool", summary)
            result = "Error: " + parse_error
            self.ui.tool_end(name or "tool", summary, result, 0.0, False)
            return self._tool_message(call, result)

        if name == "askuser":
            return self._execute_askuser(call, args, summary)

        allowed, error = self.permissions.authorize(
            name,
            args,
            summary,
            self._permission_detail(name, args),
            self.ui,
            reviewer=self.review_action,
        )
        if not allowed:
            self.ui.tool_start(name or "tool", summary)
            self.ui.tool_end(name or "tool", summary, error, 0.0, False)
            return self._tool_message(call, error)

        self.ui.tool_start(name or "tool", summary)
        started = time.perf_counter()
        result = execute_tool(name, args, self.cwd, should_stop=self._cancelled)
        elapsed = time.perf_counter() - started
        self.ui.tool_end(
            name or "tool", summary, result, elapsed, self._result_ok(result)
        )
        return self._tool_message(call, result)

    def _execute_askuser(
        self, call: dict[str, Any], args: dict[str, Any], summary: str
    ) -> dict[str, Any]:
        question = args.get("question")
        options = args.get("options")
        if not isinstance(question, str) or not question.strip():
            result = "Error: askuser requires a non-empty question."
        elif (
            not isinstance(options, list)
            or not 2 <= len(options) <= 4
            or not all(isinstance(item, str) and item.strip() for item in options)
        ):
            result = "Error: askuser requires 2-4 non-empty string options."
        else:
            clean_options = [item.strip() for item in options]
            self.ui.tool_start("askuser", summary)
            started = time.perf_counter()
            answer = self.ui.ask_user(question.strip(), clean_options)
            elapsed = time.perf_counter() - started
            result = f"User answered: {answer}"
            self.ui.tool_end("askuser", summary, result, elapsed, True)
            return self._tool_message(call, result)

        self.ui.tool_start("askuser", summary)
        self.ui.tool_end("askuser", summary, result, 0.0, False)
        return self._tool_message(call, result)

    def _execute_task_call(self, call: dict[str, Any]) -> dict[str, Any]:
        name, args, parse_error = self._parse_call(call)
        if parse_error:
            self.ui.tool_start("task", name)
            result = "Error: " + parse_error
            self.ui.tool_end("task", name, result, 0.0, False)
            return self._tool_message(call, result)

        description = str(args.get("description") or "subagent")
        summary = description[:70]
        allowed, error = self.permissions.authorize(
            "task",
            args,
            summary,
            self._permission_detail("task", args),
            self.ui,
            reviewer=self.review_action,
        )
        if not allowed:
            self.ui.tool_start("task", summary)
            self.ui.tool_end("task", summary, error, 0.0, False)
            return self._tool_message(call, error)

        self.ui.tool_start("task", summary)
        content, ok, elapsed, description = self._run_task(call)
        self.ui.tool_end("task", summary, content, elapsed, ok)
        if ok:
            answer = content.split("\n", 1)[1] if "\n" in content else content
            self.ui.info(f"  ↳ {result_headline(answer, 90)}")
        return self._tool_message(call, content)

    def _execute_parallel_tasks(
        self,
        tasks: list[tuple[int, dict[str, Any]]],
        results: list[dict[str, Any] | None],
    ) -> None:
        started = time.perf_counter()
        self.ui.tool_start("task", f"{len(tasks)} parallel subagents")
        all_ok = True
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            futures = {
                pool.submit(self._run_task, call): (index, call)
                for index, call in tasks
            }
            for future in as_completed(futures):
                index, call = futures[future]
                try:
                    content, ok, elapsed, description = future.result()
                except Exception as exc:
                    content = f"Error: subagent failed: {type(exc).__name__}: {exc}"
                    ok = False
                    elapsed = 0.0
                    description = "subagent"
                all_ok = all_ok and ok
                results[index] = self._tool_message(call, content)
                answer = content.split("\n", 1)[1] if "\n" in content else content
                self.ui.info(
                    f"  ↳ {description} · {elapsed:.1f}s · "
                    f"{'ok' if ok else 'failed'} · "
                    f"{result_headline(answer, 60)}"
                )
        elapsed = time.perf_counter() - started
        result_text = (
            f"{len(tasks)} subagents completed."
            if all_ok
            else f"{len(tasks)} subagents finished with errors."
        )
        self.ui.tool_end(
            "task",
            f"{len(tasks)} subagents",
            result_text,
            elapsed,
            all_ok,
        )

    def _run_task(self, call: dict[str, Any]):
        _, args, parse_error = self._parse_call(call)
        description = str(args.get("description") or "subagent")
        prompt = str(args.get("prompt") or "").strip()
        if parse_error or not prompt:
            content = "Error: " + (parse_error or "task prompt is empty")
            return content, False, 0.0, description

        started = time.perf_counter()
        child = self._make_subagent(description)
        answer = child.run(f"Task: {description}\n\n{prompt}")
        elapsed = time.perf_counter() - started
        if not answer.strip():
            errors = getattr(child.ui, "errors", [])
            if errors:
                answer = "Subagent errors: " + "; ".join(errors[-3:])
            elif child.last_failed:
                answer = "Subagent failed before producing a final answer."
            else:
                answer = "(subagent returned no final answer)"
        content = f"[subagent: {description}]\n{answer.strip()}"
        if len(content) > 8000:
            content = content[:8000] + "\n…[subagent output truncated]"
        return content, not child.last_failed, elapsed, description

    def _make_subagent(self, description: str) -> "Agent":
        parent_limit = max(0, _int_setting(self.config.max_steps, 0))
        child_limit = 20 if parent_limit == 0 else min(20, parent_limit)
        child_config = replace(
            self.config,
            max_steps=child_limit,
            permission=self.permissions.mode.value,
        )
        child_depth = self.subagent_depth + 1
        child_ultracode = (
            self.ultracode and child_depth < MAX_SUBAGENT_DEPTH
        )
        child_tools = [
            tool
            for tool in get_tools(
                child_ultracode, self.permissions.mode.value
            )
            if tool["function"]["name"] != "askuser"
        ]
        # Propagate the root cancellation event through every nested level.
        cancel = self.external_cancel or self._cancel
        return Agent(
            child_config,
            self.cwd,
            SubagentUI(self.ui, description),
            NullSession(self.cwd),
            client=self.client,
            system_prompt=build_subagent_prompt(
                self.cwd,
                self.permissions.mode.value,
                allow_subagents=child_ultracode,
            ),
            tools=child_tools,
            permissions=self.permissions,
            effort=self.effort,
            ultracode=child_ultracode,
            external_cancel=cancel,
            subagent_depth=child_depth,
        )


class SubagentUI(UI):
    """Silences subagent output; forwards permission prompts to the parent."""

    def __init__(self, parent: UI, label: str):
        self.parent = parent
        self.label = label
        self.verbose = False
        self.supports_modal_ask = parent.supports_modal_ask
        self.supports_modal_effort = False
        self.supports_permission_modal = parent.supports_permission_modal
        self.errors: list[str] = []

    def approve(self, tool: str, summary: str, detail: str) -> bool:
        return self.parent.approve(
            tool,
            f"[{self.label}] {summary}",
            f"[subagent: {self.label}]\n{detail}",
        )

    def error(self, message: str) -> None:
        self.errors.append(str(message))

    def warn(self, message: str) -> None:
        self.errors.append(str(message))
