"""Goal mode: plan a checklist, execute every task, then report the result."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .agent import Agent
from .ui import UI

INTERNAL_PREFIX = "[ela-internal]"

PLAN_PROMPT = """Goal:
{goal}

Create a task plan before doing the work. Inspect the repository with
read/search if useful, but do not modify files and do not use the task tool
during planning.

Output ONLY a JSON object in exactly this shape:
{{"tasks":[{{"id":1,"title":"...","detail":"...","depends_on":[]}}]}}

Rules:
- 2 to 10 small, verifiable tasks.
- depends_on lists task ids that must finish first.
- Include a verification task when appropriate.
- No prose and no code fences."""


class PlanningUI(UI):
    """Forward tool/permission events, but hide the raw planning JSON."""

    def __init__(self, parent: UI):
        self.parent = parent
        self.verbose = False
        self.supports_modal_ask = parent.supports_modal_ask
        self.supports_modal_effort = parent.supports_modal_effort
        self.supports_permission_modal = parent.supports_permission_modal

    def text(self, delta: str) -> None:
        pass

    def turn_start(self) -> None:
        self.parent.turn_start()

    def request_start(self, label: str = "thinking") -> None:
        self.parent.request_start(label)

    def request_finish(self) -> None:
        self.parent.request_finish()

    def tool_start(self, name: str, summary: str) -> None:
        self.parent.tool_start(name, summary)

    def tool_end(self, name, summary, result, elapsed, ok) -> None:
        self.parent.tool_end(name, summary, result, elapsed, ok)

    def turn_end(self, elapsed, usage, context_percent, steps, ok=True) -> None:
        self.parent.turn_end(elapsed, usage, context_percent, steps, ok)

    def info(self, message: str) -> None:
        self.parent.info(message)

    def warn(self, message: str) -> None:
        self.parent.warn(message)

    def error(self, message: str) -> None:
        self.parent.error(message)

    def println(self, text: str = "") -> None:
        self.parent.println(text)

    def set_context(self, percent: int, tokens: int) -> None:
        self.parent.set_context(percent, tokens)

    def set_model(self, model: str) -> None:
        self.parent.set_model(model)

    def approve(self, tool: str, summary: str, detail: str) -> bool:
        return self.parent.approve(tool, summary, detail)

    def ask_user(self, question: str, options: list[str]) -> str:
        return self.parent.ask_user(question, options)


def _internal(text: str) -> str:
    return f"{INTERNAL_PREFIX}\n{text}"


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return fence.group(1).strip() if fence else text


def parse_plan(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    candidates = [_strip_fences(text), text]
    data = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            break
        except (json.JSONDecodeError, TypeError):
            pass
    if data is None:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = None

    raw_tasks = None
    if isinstance(data, dict):
        raw_tasks = data.get("tasks")
    elif isinstance(data, list):
        raw_tasks = data

    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        for index, item in enumerate(raw_tasks):
            if isinstance(item, str):
                title = item.strip()
                if title:
                    tasks.append(
                        {
                            "id": index + 1,
                            "title": title,
                            "detail": "",
                            "depends_on": [],
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            title = str(
                item.get("title") or item.get("task") or item.get("name") or ""
            ).strip()
            if not title:
                continue
            try:
                task_id = int(item.get("id", index + 1))
            except (TypeError, ValueError):
                task_id = index + 1
            detail = str(item.get("detail") or item.get("description") or "").strip()
            raw_deps = item.get("depends_on") or item.get("deps") or []
            if not isinstance(raw_deps, list):
                raw_deps = [raw_deps]
            deps: list[int] = []
            for dep in raw_deps:
                try:
                    deps.append(int(dep))
                except (TypeError, ValueError):
                    pass
            tasks.append(
                {
                    "id": task_id,
                    "title": title,
                    "detail": detail,
                    "depends_on": deps,
                }
            )

    if not tasks:
        for line in text.splitlines():
            match = re.match(
                r"^\s*(?:[-*]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+)$", line
            )
            if match:
                title = match.group(1).strip()
                if title:
                    tasks.append(
                        {
                            "id": len(tasks) + 1,
                            "title": title,
                            "detail": "",
                            "depends_on": [],
                        }
                    )
        tasks = tasks[:10]

    if tasks:
        ids: set[int] = set()
        for task in tasks:
            if task["id"] in ids:
                task["id"] = max(ids | {0}) + 1
            ids.add(task["id"])
        for task in tasks:
            task["depends_on"] = [
                dep
                for dep in task["depends_on"]
                if dep in ids and dep != task["id"]
            ]
    return tasks


def plan_waves(tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    remaining = list(tasks)
    done: set[int] = set()
    waves: list[list[dict[str, Any]]] = []
    while remaining:
        ready = [
            task
            for task in remaining
            if all(dep in done for dep in task.get("depends_on", []))
        ]
        if not ready:
            # Dependency cycle: keep making progress in plan order.
            ready = [remaining[0]]
        waves.append(ready)
        for task in ready:
            done.add(task["id"])
        remaining = [task for task in remaining if task["id"] not in done]
    return waves


def parse_task_status(reply: str) -> tuple[bool, str]:
    match = re.search(
        r"TASK\s*STATUS:\s*(done|failed)\s*(?::\s*(.*))?",
        reply,
        re.IGNORECASE,
    )
    if match:
        ok = match.group(1).lower() == "done"
        summary = (match.group(2) or "").strip()
        return ok, summary
    return True, summarize_reply(reply)


def summarize_reply(reply: str, limit: int = 240) -> str:
    lines = [
        line.strip()
        for line in reply.splitlines()
        if line.strip() and not line.strip().upper().startswith("TASK STATUS:")
    ]
    text = " ".join(lines)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _deps_text(task: dict[str, Any], results: dict[int, str]) -> str:
    lines = []
    for dep in task.get("depends_on", []):
        if dep in results:
            lines.append(f"- {results[dep][:800]}")
    return "\n".join(lines)


def _run_task_main(
    agent: Agent,
    ui: UI,
    goal: str,
    task: dict[str, Any],
    index: int,
    total: int,
    results: dict[int, str],
) -> tuple[bool, str, float]:
    started = time.perf_counter()
    deps = _deps_text(task, results)
    dependency_block = f"\nCompleted prerequisites:\n{deps}\n" if deps else ""
    prompt = _internal(
        f"Goal:\n{goal}\n\n"
        f"Task {index}/{total}: {task['title']}\n"
        f"{task.get('detail') or ''}\n"
        f"{dependency_block}\n"
        "Complete this task now using tools. Do not produce the final goal "
        "summary yet. End with a line exactly like:\n"
        "TASK STATUS: done\n"
        "or\n"
        "TASK STATUS: failed: <reason>"
    )
    reply = agent.run(prompt)
    elapsed = time.perf_counter() - started
    if agent.last_failed:
        return False, "agent failed while running the task", elapsed
    ok, summary = parse_task_status(reply)
    return ok, summary or summarize_reply(reply), elapsed


def _run_task_subagent(
    agent: Agent,
    goal: str,
    task: dict[str, Any],
    index: int,
    total: int,
    results: dict[int, str],
) -> tuple[bool, str, float]:
    started = time.perf_counter()
    deps = _deps_text(task, results)
    dependency_block = f"\nCompleted prerequisites:\n{deps}\n" if deps else ""
    prompt = (
        f"Overall goal:\n{goal}\n\n"
        f"Task {index}/{total}: {task['title']}\n"
        f"{task.get('detail') or ''}\n"
        f"{dependency_block}\n"
        "Complete this task now using tools. End with a line exactly like:\n"
        "TASK STATUS: done\n"
        "or\n"
        "TASK STATUS: failed: <reason>"
    )
    answer, child_ok = agent.run_subagent(f"goal task {index}: {task['title']}", prompt)
    elapsed = time.perf_counter() - started
    if not child_ok:
        return False, answer[:300] or "subagent failed", elapsed
    ok, summary = parse_task_status(answer)
    return ok, summary or summarize_reply(answer), elapsed


def run_goal(agent: Agent, ui: UI, goal: str) -> str:
    goal = goal.strip()
    if not goal:
        return ""
    ui.goal_start(goal)

    parent_ui = agent.ui
    planner = PlanningUI(parent_ui)
    agent.ui = planner
    try:
        plan_reply = agent.run(_internal(PLAN_PROMPT.format(goal=goal)))
    finally:
        agent.ui = parent_ui

    if agent.cancelled():
        ui.warn("goal interrupted")
        ui.goal_finish(False, 0, 0)
        return ""
    if agent.last_failed:
        ui.error("goal planning failed")
        ui.goal_finish(False, 0, 0)
        return ""

    tasks = parse_plan(plan_reply)
    if not tasks:
        ui.error("goal planning returned no parseable tasks")
        ui.goal_finish(False, 0, 0)
        return ""

    # The raw plan JSON is only scaffolding; keep the session transcript clean.
    if agent.messages and agent.messages[-1].get("role") == "assistant":
        agent.messages[-1]["content"] = "[ela-internal] plan created"
        agent.session.rewrite(agent.messages)

    ui.goal_plan(goal, tasks)
    total = len(tasks)
    index_of = {task["id"]: index + 1 for index, task in enumerate(tasks)}
    results: dict[int, str] = {}
    completed = 0
    failed = 0

    for wave in plan_waves(tasks):
        if agent.cancelled():
            break
        if agent.ultracode:
            with ThreadPoolExecutor(max_workers=min(4, len(wave))) as pool:
                futures = {
                    pool.submit(
                        _run_task_subagent,
                        agent,
                        goal,
                        task,
                        index_of[task["id"]],
                        total,
                        results,
                    ): task
                    for task in wave
                }
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        ok, summary, elapsed = future.result()
                    except Exception as exc:
                        ok = False
                        summary = f"{type(exc).__name__}: {exc}"
                        elapsed = 0.0
                    if ok:
                        completed += 1
                        results[task["id"]] = summary
                    else:
                        failed += 1
                    ui.goal_task_end(
                        index_of[task["id"]],
                        total,
                        task["title"],
                        ok,
                        elapsed,
                        summary,
                    )
            if failed:
                break
        else:
            for task in wave:
                if agent.cancelled():
                    break
                index = index_of[task["id"]]
                ui.goal_task_start(index, total, task["title"])
                ok, summary, elapsed = _run_task_main(
                    agent, ui, goal, task, index, total, results
                )
                if ok:
                    completed += 1
                    results[task["id"]] = summary
                else:
                    failed += 1
                ui.goal_task_end(
                    index, total, task["title"], ok, elapsed, summary
                )
                if not ok:
                    break
            if failed:
                break

    if agent.cancelled():
        ui.warn("goal interrupted")
        ui.goal_finish(False, completed, total)
        return ""
    if failed or completed < total:
        ui.error(f"goal incomplete: {completed}/{total} tasks completed")
        ui.goal_finish(False, completed, total)
        return ""

    result_lines = []
    for task in tasks:
        result_lines.append(
            f"- {task['title']}: {results.get(task['id'], '')[:1200]}"
        )
    final_prompt = _internal(
        f"Goal:\n{goal}\n\n"
        "All tasks are complete. Produce the final result for this goal.\n"
        "Use the task results below. Be concise and concrete; do not repeat the "
        "checklist unless it helps.\n\n"
        "Task results:\n" + "\n".join(result_lines)
    )
    result = agent.run(final_prompt)
    ui.goal_finish(not agent.last_failed, completed, total)
    return result if not agent.last_failed else ""
