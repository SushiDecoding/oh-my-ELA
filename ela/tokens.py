"""Token accounting and context trimming.

tiktoken is optional: when present, counting is exact (cl100k_base); otherwise
we fall back to a cheap chars/4 estimate. The runtime only uses estimates for
context pressure, so the hot path never imports tiktoken.
"""

from __future__ import annotations

import re
from typing import Any

_encoder = None
_encoder_loaded = False
_CJK = re.compile(r"[　-ヿ㐀-䶿一-鿿豈-﫿가-힯]")


def rough_tokens(text: str) -> int:
    """Fast language-aware estimate: CJK chars ≈ 1 token, others ≈ 4 chars."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + max(1, (len(text) - cjk) // 4)


def _get_encoder():
    global _encoder, _encoder_loaded
    if not _encoder_loaded:
        _encoder_loaded = True
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Fast estimate for a single chat message, including tool calls."""
    content = message.get("content")
    tokens = rough_tokens(content) if isinstance(content, str) else 0
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        tokens += rough_tokens(
            (fn.get("name") or "") + (fn.get("arguments") or "")
        )
    return max(1, tokens) + 6


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def _groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split messages into user-turn groups; system stays at index 0."""
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "user" or not groups:
            groups.append([message])
        else:
            groups[-1].append(message)
    return groups


def trim_messages(
    messages: list[dict[str, Any]], budget_tokens: int
) -> tuple[list[dict[str, Any]], int]:
    """Drop the oldest complete turns until under budget.

    Keeps the system message and at least the two most recent turns. If a
    single oversized tool result still blows the budget, its middle is elided.
    Returns (messages, number_of_dropped_turns).
    """
    if len(messages) <= 1 or estimate_tokens(messages) <= budget_tokens:
        return messages, 0

    system, rest = messages[0], messages[1:]
    groups = _groups(rest)
    dropped = 0
    while len(groups) > 2 and estimate_tokens([system, *rest]) > budget_tokens:
        rest = [m for group in groups[1:] for m in group]
        groups = groups[1:]
        dropped += 1

    if estimate_tokens([system, *rest]) > budget_tokens:
        # Elide the middle of the largest tool outputs, oldest first.
        tool_messages = [m for m in rest if m.get("role") == "tool"]
        for message in tool_messages:
            content = message.get("content")
            if isinstance(content, str) and len(content) > 6000:
                message["content"] = (
                    content[:2000]
                    + "\n...[tool output elided to fit context]...\n"
                    + content[-2000:]
                )
                if estimate_tokens([system, *rest]) <= budget_tokens:
                    break

    return [system, *rest], dropped


def format_int(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m"
    if n >= 1000:
        return f"{n / 1000:.1f}k" if n < 10_000 else f"{n / 1000:.0f}k"
    return str(n)
