"""AutoReview safety subagent: one JSON-only model call per risky operation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Config
from .retry import retry_call

REVIEW_SYSTEM_PROMPT = """\
You are a security reviewer for a local coding agent.
The user enabled AutoReview Mode. File reads are already allowed.
Decide whether the following write/edit/shell operation is safe to run
without asking the user.
Workspace: {cwd}

Be conservative. Mark unsafe for: destructive deletion, paths outside the
workspace, secrets or credentials, network exfiltration, privilege escalation,
system file changes, and obfuscated or ambiguous shell commands.
If uncertain, set safe to false.

Return ONLY a JSON object in this exact shape:
{{"safe": true, "reason": "one short sentence"}}
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        return text[start : end + 1]
    return text


def _parse_review(text: str) -> tuple[bool, str]:
    try:
        data = json.loads(_extract_json(text))
    except (json.JSONDecodeError, TypeError):
        return False, "reviewer returned invalid JSON"
    if not isinstance(data, dict):
        return False, "reviewer JSON was not an object"
    safe = data.get("safe")
    if isinstance(safe, str):
        safe = safe.strip().lower() in {"true", "yes", "1", "allow"}
    else:
        safe = bool(safe)
    reason = str(data.get("reason") or "").strip()
    if len(reason) > 240:
        reason = reason[:239] + "…"
    return safe, reason


def review_action(
    client,
    config: Config,
    cwd: Path,
    tool: str,
    args: dict[str, Any],
    summary: str,
    detail: str,
) -> tuple[bool, str]:
    """Run the JSON safety reviewer. On any failure, return unsafe."""
    system = REVIEW_SYSTEM_PROMPT.format(cwd=str(cwd))
    user = (
        f"Tool: {tool}\n"
        f"Summary: {summary}\n"
        f"Details:\n{str(detail)[:3000]}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    base = {
        "model": config.model,
        "messages": messages,
        "stream": False,
    }
    def ask() -> str:
        try:
            response = client.chat.completions.create(
                **base,
                max_tokens=220,
                response_format={"type": "json_object"},
            )
        except Exception:
            # Not all OpenAI-compatible endpoints accept JSON mode or max_tokens.
            # The fallback lives inside the attempt, so every retry starts with
            # JSON mode again before degrading.
            response = client.chat.completions.create(**base)
        return response.choices[0].message.content or ""

    try:
        content = retry_call(ask, policy=config.retry_policy())
    except Exception as exc:
        # Fail closed: a reviewer we cannot reach never approves an action.
        return False, f"reviewer unavailable: {type(exc).__name__}"
    return _parse_review(content)
