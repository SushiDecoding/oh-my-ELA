"""System prompt and tool schemas.

The core prompt is budgeted per variant: base (system + 6 tools) at 1200 tokens
and the ultracode/AutoReview maximum at 1500, the hard ceiling. Both leave
headroom instead of pinning the prompt to a single tight limit. `ela --tokens`
and tests/test_prompt_budget.py enforce the two caps.
"""

from __future__ import annotations

import json
import platform
from datetime import date
from pathlib import Path

SYSTEM_PROMPT = """\
You are ela, a precise, fast terminal coding agent. Be terse and act.

Rules:
- Read/search before editing. Never guess file contents, paths, or command results.
- Prefer small, targeted edits; match existing style and formatting.
- Use pwsh (PowerShell 7) for tests, builds, git, and inspection. Write PowerShell syntax, not POSIX sh. Verify changes; fix failures.
- After batch or multi-file edits, run a syntax check or the tests.
- Never commit, push, or create extra files unless asked.
- Never print API keys, credentials, or .env contents; never commit them.
- Batch independent tool calls in one message.
- On failure or denial, change approach; never repeat an identical call.
- Ask only when a decision blocks progress, with 2-4 concrete options. Never ask the user to authorize your own actions.
- For multi-step work, keep a short checklist and finish every step before reporting.
- Report blockers and incomplete work plainly. No filler, emojis, or recaps.
- Match the user's language. When done, stop.

Environment: {cwd} · {os_name} · {today}{context}"""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read text files with line numbers. Read before editing. "
                "Capped at 400 lines; binary rejected; directories need "
                "search mode=files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files to read.",
                    },
                    "start": {
                        "type": "integer",
                        "description": "1-based first line; applies to all paths.",
                    },
                    "end": {
                        "type": "integer",
                        "description": "Inclusive last line; applies to all paths.",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": (
                "Create or overwrite a file with exact content. Parent directories "
                "are created; prefer edit for small changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": (
                "Replace exact text in a file. old must match including whitespace; "
                "read first. Fails if missing or ambiguous; set replace_all only "
                "when intentional."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence.",
                    },
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pwsh",
            "description": (
                "Run a PowerShell 7 command (PowerShell syntax, not bash) in the "
                "workspace. Use for tests, builds, git, and Windows-native tasks. "
                "Returns output and exit code, truncated to ~16k chars. stdin is "
                "closed; timeout kills the process tree."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds; default 120, max 600.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search files by content regex, path glob, or match count. Files "
                "glob is recursive. Prefer over shell grep. Skips .git, "
                "node_modules, .venv, binaries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex for content/count; glob for files.",
                    },
                    "path": {"type": "string", "description": "Root; default ."},
                    "mode": {
                        "type": "string",
                        "enum": ["content", "files", "count"],
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "askuser",
            "description": (
                "Ask the user to pick 2-4 concrete options when a decision blocks "
                "progress. Returns the choice or custom text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "2-4 concrete choices.",
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
]

TASK_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "task",
        "description": (
            "Run a focused subagent on self-contained work. Issue multiple "
            "calls in one message for parallel independent tasks. Returns each "
            "final answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short label."},
                "prompt": {"type": "string", "description": "Complete task for the subagent."},
            },
            "required": ["description", "prompt"],
        },
    },
}

CONTEXT_FILES = ("ELA.md", "AGENTS.md")
MAX_CONTEXT_CHARS = 6000

EFFORT_LINES = {
    "low": "low — act directly; minimal deliberation.",
    "medium": "medium — act directly; brief reasoning only when needed.",
    "high": "high — reason carefully; verify edge cases before acting.",
}

PERMISSION_LINES = {
    "full": "Auto Mode — full access.",
    "ask": "Ask Mode — reads within the workspace; writes/edits/pwsh need approval.",
    "readonly": "Readonly Mode — read/search/askuser only.",
    "autoreview": (
        "AutoReview Mode — Ask Mode + a JSON safety subagent; unsafe actions ask."
    ),
}

SUBAGENT_SYSTEM_PROMPT = """\
You are a focused subagent working for another agent.
Complete the assigned task with tools. Read before editing.
Work independently; do not ask questions. Return concise results, not plans.
When the caller requests a TASK STATUS line, end with it.
Environment: {cwd} · permissions: {permission}"""


def get_tools(
    ultracode: bool = False, permission: str | None = None
) -> list[dict]:
    tools = (
        [
            tool
            for tool in TOOLS
            if tool["function"]["name"] in ("read", "search", "askuser")
        ]
        if permission == "readonly"
        else list(TOOLS)
    )
    if ultracode:
        tools.append(TASK_TOOL)
    return tools


def build_subagent_prompt(
    cwd: Path, permission: str, allow_subagents: bool = False
) -> str:
    prompt = SUBAGENT_SYSTEM_PROMPT.format(
        cwd=str(cwd), permission=permission
    )
    if allow_subagents:
        prompt += (
            "\nYou may use task to fan out independent subtasks in parallel. "
            "Do not ask questions."
        )
    return prompt


def read_project_context(cwd: Path) -> str:
    """Return project instructions from ELA.md / AGENTS.md, if present.

    Both files are concatenated under the shared character cap so an AGENTS.md
    is never silently ignored when an ELA.md also exists.
    """
    chunks: list[str] = []
    spent = 0
    for name in CONTEXT_FILES:
        path = cwd / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        room = MAX_CONTEXT_CHARS - spent
        if room <= 0:
            chunks.append("[context truncated: budget exhausted]")
            break
        if len(text) > room:
            text = text[:room] + "\n[context truncated]"
        chunks.append(f"\n\nProject instructions ({name}):\n{text}")
        spent += len(text)
    return "".join(chunks)


def build_system_prompt(
    cwd: Path,
    context: str | None = None,
    effort: str | None = None,
    ultracode: bool = False,
    permission: str | None = None,
) -> str:
    if context is None:
        context = read_project_context(cwd)
    prompt = SYSTEM_PROMPT.format(
        cwd=str(cwd),
        os_name=f"{platform.system()} {platform.release()}".strip(),
        today=date.today().isoformat(),
        context=context,
    )
    extras = []
    if permission:
        extras.append(f"Permissions: {PERMISSION_LINES.get(permission, permission)}")
    if effort:
        extras.append(f"Effort: {EFFORT_LINES.get(effort, effort)}")
    if ultracode:
        extras.append(
            "Ultracode: use task for independent work; issue multiple task calls "
            "in one message to run subagents in parallel."
        )
    if extras:
        prompt += "\n\n" + "\n".join(extras)
    return prompt


def tool_specs_json(
    ultracode: bool = False, permission: str | None = None
) -> str:
    """Compact JSON used for token accounting (mirrors what the API receives)."""
    return json.dumps(
        get_tools(ultracode, permission), ensure_ascii=False, separators=(",", ":")
    )
