"""Configuration, paths, and environment resolution."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ELA_HOME = Path(os.environ.get("ELA_HOME", Path.home() / ".ela")).expanduser()
CONFIG_PATH = ELA_HOME / "config.json"
SESSIONS_DIR = ELA_HOME / "sessions"
HISTORY_PATH = ELA_HOME / "history"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    model: str = "gpt-4.1-mini"
    base_url: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    context_window: int = 128_000
    max_steps: int = 0  # 0 = no limit
    stream_usage: bool = True
    verbose: bool = False
    timeout: float = 300.0
    permission: str = "ask"
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 20.0

    def retry_policy(self) -> RetryPolicy:
        """Map the retry fields onto a RetryPolicy (see .retry)."""
        from .retry import RetryPolicy

        return RetryPolicy(
            max_attempts=max(1, _as_int(self.max_retries, 3)),
            base_delay=max(0.0, _as_float(self.retry_base_delay, 1.0)),
            max_delay=max(0.0, _as_float(self.retry_max_delay, 20.0)),
        )


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def ensure_home() -> None:
    ELA_HOME.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


_STRING_FIELDS = {"model", "base_url", "api_key", "permission"}
_INT_FIELDS = {"context_window", "max_steps", "max_retries", "max_tokens"}
_FLOAT_FIELDS = {"temperature", "timeout", "retry_base_delay", "retry_max_delay"}
_BOOL_FIELDS = {"stream_usage", "verbose"}


def _coerce_config_value(
    key: str, value: object, default: object
) -> tuple[object, bool]:
    """Return (value, ok). ok is False when a hand-edited value is unusable."""
    if value is None:
        return default, True
    if key in _INT_FIELDS:
        try:
            return int(value), True  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default, False
    if key in _FLOAT_FIELDS:
        try:
            return float(value), True  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default, False
    if key in _BOOL_FIELDS:
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", ""}, True
        if isinstance(value, (bool, int)):
            return bool(value), True
        return default, False
    if key in _STRING_FIELDS:
        if isinstance(value, str):
            return value, True
        return default, False
    return default, True


def load_config() -> Config:
    config = Config()
    if not CONFIG_PATH.is_file():
        return config
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ela: ignoring bad config {CONFIG_PATH}: {exc}", file=sys.stderr)
        return config
    if not isinstance(data, dict):
        return config
    defaults = Config()
    known = {field.name for field in fields(Config)}
    for key, value in data.items():
        if key not in known:
            continue
        coerced, ok = _coerce_config_value(key, value, getattr(defaults, key))
        if not ok:
            print(
                f"ela: ignoring invalid config value {key}={value!r} in {CONFIG_PATH}",
                file=sys.stderr,
            )
            continue
        setattr(config, key, coerced)
    return config


def save_config(config: Config) -> None:
    ensure_home()
    data = {key: value for key, value in asdict(config).items() if value is not None}
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        # The file holds a plaintext API key; keep it owner-only.
        os.chmod(temp, 0o600)
    os.replace(temp, CONFIG_PATH)


def apply_environment(config: Config) -> Config:
    """Env vars win over the config file."""
    model = os.environ.get("ELA_MODEL")
    base_url = os.environ.get("ELA_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("ELA_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if model:
        config.model = model
    if base_url:
        config.base_url = base_url
    if api_key:
        config.api_key = api_key
    if os.environ.get("ELA_STREAM_USAGE", "").lower() in {"0", "false", "no"}:
        config.stream_usage = False
    permission = os.environ.get("ELA_PERMISSION")
    if permission:
        config.permission = permission
    retries = _as_int(os.environ.get("ELA_MAX_RETRIES"), 0)
    if retries > 0:
        config.max_retries = retries
    base_delay = _as_float(os.environ.get("ELA_RETRY_BASE_DELAY"), -1.0)
    if base_delay >= 0:
        config.retry_base_delay = base_delay
    max_delay = _as_float(os.environ.get("ELA_RETRY_MAX_DELAY"), -1.0)
    if max_delay >= 0:
        config.retry_max_delay = max_delay
    return config


def _is_local(base_url: str | None) -> bool:
    if not base_url:
        return False
    lowered = base_url.lower()
    return "localhost" in lowered or "127.0.0.1" in lowered or "0.0.0.0" in lowered


def has_credentials(config: Config) -> bool:
    """True when the endpoint can authenticate without prompting."""
    return bool(config.api_key) or _is_local(config.base_url)


def require_api_key(config: Config) -> str:
    key = config.api_key
    if key:
        return key
    if _is_local(config.base_url):
        return "not-needed"
    raise ConfigError(
        "No API key. Set OPENAI_API_KEY (or ELA_API_KEY), or put api_key in "
        f"{CONFIG_PATH}.\nFor OpenAI-compatible endpoints, also set "
        "OPENAI_BASE_URL (or ELA_BASE_URL) and ELA_MODEL."
    )


def init_config() -> Path:
    ensure_home()
    if not CONFIG_PATH.exists():
        save_config(Config())
    return CONFIG_PATH


def display_path(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def shorten_cwd(cwd: Path, limit: int = 42) -> str:
    text = display_path(cwd)
    if len(text) <= limit:
        return text
    parts = text.split("/")
    if len(parts) >= 3:
        text = ".../" + "/".join(parts[-2:])
    return text if len(text) <= limit else text[: limit - 1] + "…"
