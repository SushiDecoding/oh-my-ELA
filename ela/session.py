"""JSONL session persistence.

Each session is an append-only transcript: one metadata line followed by one
message per line. Files live under ~/.ela/sessions and are scoped to a cwd.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import SESSIONS_DIR, ensure_home


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


@dataclass
class SessionInfo:
    id: str
    path: Path
    cwd: str
    model: str
    created: str
    messages: int
    snippet: str


class NullSession:
    """No-op session used by --no-save."""

    id = "ephemeral"
    path = None
    model = ""

    def __init__(self, cwd: Path):
        self.cwd = cwd

    def append(self, message: dict[str, Any]) -> None:
        pass

    def rewrite(self, messages: Iterable[dict[str, Any]]) -> None:
        pass

    def load(self) -> list[dict[str, Any]]:
        return []

    def update_model(self, model: str) -> None:
        pass


class Session:
    def __init__(self, cwd: Path, model: str = "", session_id: str | None = None):
        ensure_home()
        self.cwd = cwd.resolve()
        self.id = session_id or _new_id()
        self.model = model
        self.path = SESSIONS_DIR / f"{self.id}.jsonl"
        self._created = False
        if not self.path.exists():
            self._write_meta()
            self._created = True

    def _write_meta(self) -> None:
        meta = {
            "type": "meta",
            "id": self.id,
            "cwd": str(self.cwd),
            "model": self.model,
            "created": _now(),
        }
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")

    def append(self, message: dict[str, Any]) -> None:
        record = {"type": "message", "message": message}
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def rewrite(self, messages: Iterable[dict[str, Any]]) -> None:
        """Rewrite the transcript. The system prompt is never persisted: it is
        rebuilt from the current config on every start, so storing it would
        only go stale (and inflate the message count shown by /resume).
        """
        kept = list(messages)
        if kept and kept[0].get("role") == "system":
            kept = kept[1:]
        meta = {
            "type": "meta",
            "id": self.id,
            "cwd": str(self.cwd),
            "model": self.model,
            "created": _read_created(self.path),
            "updated": _now(),
        }
        temp = self.path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for message in kept:
                handle.write(
                    json.dumps({"type": "message", "message": message}, ensure_ascii=False)
                    + "\n"
                )
        os.replace(temp, self.path)

    def load(self) -> list[dict[str, Any]]:
        return _read_messages(self.path)

    def update_model(self, model: str) -> None:
        """Record the model in the session metadata for later resumes."""
        self.model = model
        if not self.path.exists():
            return
        try:
            self.rewrite(self.load())
        except OSError:
            pass

    def __repr__(self) -> str:
        return f"Session({self.id}, {self.path})"


def _read_created(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            meta = json.loads(handle.readline())
        return meta.get("created", _now())
    except (OSError, json.JSONDecodeError):
        return _now()


def _read_messages(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "message" and isinstance(record.get("message"), dict):
                    messages.append(record["message"])
    except OSError:
        return []
    return messages


def _read_info(path: Path) -> SessionInfo | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            first = handle.readline()
        meta = json.loads(first)
    except (OSError, json.JSONDecodeError):
        return None
    messages = _read_messages(path)
    snippet = ""
    for message in messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            snippet = " ".join(message["content"].split())
            break
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return SessionInfo(
        id=meta.get("id") or path.stem,
        path=path,
        cwd=meta.get("cwd", ""),
        model=meta.get("model", ""),
        created=meta.get("created", ""),
        messages=len(messages),
        snippet=snippet,
    )


def _same_cwd(a: str, b: Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(b.resolve()))
    except (OSError, ValueError):
        return False


def list_sessions(cwd: Path, limit: int = 30) -> list[SessionInfo]:
    ensure_home()
    if not SESSIONS_DIR.is_dir():
        return []
    paths = sorted(
        SESSIONS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    infos: list[SessionInfo] = []
    for path in paths:
        if len(infos) >= limit:
            break
        info = _read_info(path)
        if info and info.messages and _same_cwd(info.cwd, cwd):
            infos.append(info)
    return infos


def latest_session(cwd: Path) -> SessionInfo | None:
    sessions = list_sessions(cwd, limit=1)
    return sessions[0] if sessions else None


def open_session(info: SessionInfo) -> Session:
    model = info.model
    session = Session(Path(info.cwd), model=model, session_id=info.id)
    return session


def resume_by_id(cwd: Path, session_id: str) -> Session | None:
    for info in list_sessions(cwd, limit=500):
        if info.id == session_id or info.id.startswith(session_id):
            return open_session(info)
    return None
