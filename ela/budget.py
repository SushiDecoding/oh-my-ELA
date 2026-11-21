"""Prompt budget accounting (core system prompt + tool schemas).

Two caps, because the prompt has two shapes:

- BASE_BUDGET: the six default tools with the standard mode lines.
- MAX_BUDGET: high effort + ultracode + AutoReview, i.e. the `task` tool and
  its extra prompt lines included. 1500 tokens is the hard ceiling.

Both intentionally leave generous headroom. A prompt squeezed against its
ceiling makes every tool-description edit cannibalize another one, which
produces worse descriptions. Project context (ELA.md / AGENTS.md) is
user-supplied and sits outside both caps.
"""

from __future__ import annotations

from pathlib import Path

from .prompt import build_system_prompt, tool_specs_json
from .tokens import count_tokens

BASE_BUDGET = 1200
MAX_BUDGET = 1500


def _measure(
    cwd: Path, effort: str, ultracode: bool, permission: str
) -> tuple[int, int]:
    system = build_system_prompt(
        cwd, context="", effort=effort, ultracode=ultracode, permission=permission
    )
    return count_tokens(system), count_tokens(tool_specs_json(ultracode))


def breakdown(cwd: Path) -> dict[str, int]:
    base_system, base_tools = _measure(cwd, "medium", False, "ask")
    max_system, max_tools = _measure(cwd, "high", True, "autoreview")
    base = base_system + base_tools
    maximum = max_system + max_tools
    return {
        "base_system": base_system,
        "base_tools": base_tools,
        "base": base,
        "base_budget": BASE_BUDGET,
        "base_left": BASE_BUDGET - base,
        "max_system": max_system,
        "max_tools": max_tools,
        "max": maximum,
        "max_budget": MAX_BUDGET,
        "max_left": MAX_BUDGET - maximum,
    }
