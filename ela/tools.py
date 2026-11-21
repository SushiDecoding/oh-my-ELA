"""Tool implementations.

Design follows two rules borrowed from dsh-ops:
  1. bounded, line-numbered output (no whole-file dumps),
  2. failures are returned as values, never raised into the agent loop.
"""

from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .paths import resolve_path

MAX_READ_LINES = 400
MAX_READ_CHARS = 24_000
MAX_LINE_CHARS = 400
MAX_TOOL_CHARS = 16_000
MAX_SEARCH_RESULTS = 100
MAX_COUNT_ROWS = 50
MAX_SEARCH_FILES = 200
MAX_COMMAND_SECONDS = 600
MAX_CAPTURE_BYTES = 2 * 1024 * 1024

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    "dist",
    "build",
    "target",
    ".next",
}
SKIP_DIRS_LOWER = {name.lower() for name in SKIP_DIRS}


if os.name == "nt":
    import ctypes

    class _JobBasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _JobIoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JobExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobBasicLimits),
            ("IoInfo", _JobIoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateJobObjectW.restype = ctypes.c_void_p
    _KERNEL32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    _KERNEL32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    _KERNEL32.AssignProcessToJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _KERNEL32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]

    class _WindowsJob:
        """Kill-on-close job object; reliably terminates the whole child tree."""

        def __init__(self):
            self.handle = None
            try:
                handle = _KERNEL32.CreateJobObjectW(None, None)
                if not handle:
                    return
                limits = _JobExtendedLimits()
                limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
                if not _KERNEL32.SetInformationJobObject(
                    handle,
                    9,  # JobObjectExtendedLimitInformation
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                ):
                    _KERNEL32.CloseHandle(handle)
                    return
                self.handle = handle
            except Exception:
                self.handle = None

        def assign(self, proc) -> None:
            if not self.handle:
                return
            try:
                _KERNEL32.AssignProcessToJobObject(
                    self.handle, ctypes.c_void_p(int(proc._handle))
                )
            except Exception:
                pass

        def terminate(self) -> None:
            if self.handle:
                try:
                    _KERNEL32.TerminateJobObject(self.handle, 1)
                except Exception:
                    pass

        def close(self) -> None:
            if self.handle:
                try:
                    _KERNEL32.CloseHandle(self.handle)
                except Exception:
                    pass
                self.handle = None


class ToolError(Exception):
    pass


def _as_bool(value: Any) -> bool:
    """Accept booleans and the string spellings models sometimes send."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _trim(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit * 3 // 5
    tail = limit - head
    return (
        text[:head]
        + f"\n...[truncated {len(text) - limit} chars]...\n"
        + text[-tail:]
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise ToolError(f"path is a directory: {path}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ela-", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if path.exists():
            try:
                shutil.copymode(path, tmp_path)
            except OSError:
                pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _read_one(path: Path, display: str, start: int, end: int | None) -> str:
    if not path.exists():
        raise ToolError(f"file not found: {display}")
    if path.is_dir():
        raise ToolError(f"is a directory (use search mode=files): {display}")
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError(f"binary file: {display}")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return f"{display} (empty)"
    if start < 1:
        start = 1
    if end is None:
        end = start + MAX_READ_LINES - 1
    end = min(end, total)
    if start > total:
        raise ToolError(f"start {start} is past end of file ({total} lines): {display}")
    if end < start:
        raise ToolError(f"invalid range {start}-{end}: {display}")
    width = len(str(end))
    body = []
    for number in range(start, end + 1):
        line = lines[number - 1]
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "…"
        body.append(f"{number:>{width}}| {line}")
    header = f"{display} (lines {start}-{end} of {total})"
    if end < total:
        header += " · use start/end for more"
    return header + "\n" + "\n".join(body)


def tool_read(args: dict[str, Any], cwd: Path) -> str:
    raw_paths = args.get("paths", args.get("path"))
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ToolError("paths must be a non-empty array of strings")
    start = int(args.get("start") or 1)
    end = args.get("end")
    end = int(end) if end is not None else None

    chunks: list[str] = []
    spent = 0
    for raw in raw_paths:
        path = resolve_path(cwd, str(raw))
        display = _display_path(path, cwd)
        try:
            chunk = _read_one(path, display, start, end)
        except ToolError as exc:
            chunk = f"Error: {exc}"
        if spent + len(chunk) > MAX_READ_CHARS:
            room = MAX_READ_CHARS - spent
            if room > 200:
                chunks.append(chunk[:room] + "\n...[read budget exhausted]")
            break
        chunks.append(chunk)
        spent += len(chunk)
    return "\n\n".join(chunks)


def tool_write(args: dict[str, Any], cwd: Path) -> str:
    raw = args.get("path")
    content = args.get("content")
    if not isinstance(raw, str) or not raw:
        raise ToolError("path must be a non-empty string")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    path = resolve_path(cwd, raw)
    existed = path.exists()
    _atomic_write(path, content)
    verb = "Overwrote" if existed else "Wrote"
    return (
        f"{verb} {_display_path(path, cwd)} "
        f"({_line_count(content)} lines, {len(content.encode('utf-8'))} bytes)."
    )


def _find_all(haystack: str, needle: str) -> list[int]:
    hits, pos = [], 0
    while True:
        index = haystack.find(needle, pos)
        if index < 0:
            return hits
        hits.append(index)
        pos = index + max(1, len(needle))


def tool_edit(args: dict[str, Any], cwd: Path) -> str:
    raw = args.get("path")
    old = args.get("old")
    new = args.get("new")
    if not isinstance(raw, str) or not raw:
        raise ToolError("path must be a non-empty string")
    if not isinstance(old, str) or old == "":
        raise ToolError("old must be a non-empty string")
    if not isinstance(new, str):
        raise ToolError("new must be a string")
    path = resolve_path(cwd, raw)
    display = _display_path(path, cwd)
    if not path.exists():
        raise ToolError(f"file not found: {display}")
    if path.is_dir():
        raise ToolError(f"is a directory: {display}")

    with open(path, "r", encoding="utf-8", newline="") as handle:
        content = handle.read()

    needle, replacement = old, new
    hits = _find_all(content, needle)
    if not hits and "\r\n" in content:
        old_lf = old.replace("\r\n", "\n")
        new_lf = new.replace("\r\n", "\n")
        needle = old_lf.replace("\n", "\r\n")
        replacement = new_lf.replace("\n", "\r\n")
        hits = _find_all(content, needle)

    if not hits:
        hint = ""
        if " ".join(old.split()) and " ".join(old.split()) in " ".join(content.split()):
            hint = " Text exists but whitespace differs; re-read the file and match exactly."
        raise ToolError(f"old text not found in {display}.{hint}")
    replace_all = _as_bool(args.get("replace_all"))
    if len(hits) > 1 and not replace_all:
        line_numbers = [content[:index].count("\n") + 1 for index in hits[:5]]
        raise ToolError(
            f"old text appears {len(hits)} times in {display} "
            f"(lines {line_numbers}). Add surrounding context or set replace_all=true."
        )

    count = len(hits) if replace_all else 1
    updated = content.replace(needle, replacement, -1 if replace_all else 1)
    _atomic_write(path, updated)
    return f"Edited {display} ({count} replacement{'s' if count != 1 else ''})."


PWSH_PRELUDE = (
    "$ErrorActionPreference='Stop'\n"
    "$ProgressPreference='SilentlyContinue'\n"
    "try { $ErrorView='ConciseView' } catch {}\n"
    "if (Get-Variable PSStyle -ErrorAction SilentlyContinue) "
    "{ $PSStyle.OutputRendering='PlainText' }\n"
    "try { [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false) } "
    "catch {}\n"
    "try { $OutputEncoding=[Text.UTF8Encoding]::new($false) } catch {}\n"
)
PWSH_EPILOGUE = (
    "\nif ($?) { exit 0 } "
    "elseif ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 }"
)


def _which_exe(name: str) -> str | None:
    found = shutil.which(name)
    if not found:
        return None
    if os.name == "nt" and Path(found).suffix.lower() != ".exe":
        return None
    return found


def _first_existing(paths: list[Path]) -> str | None:
    for path in paths:
        if path.is_file():
            return str(path)
    return None


def _find_pwsh() -> str | None:
    """Prefer PowerShell 7 preview, then stable PowerShell 7."""
    override = os.environ.get("ELA_PWSH")
    if override and Path(override).exists():
        return override

    if os.name != "nt":
        return _which_exe("pwsh-preview") or _which_exe("pwsh")

    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")),
    ]
    preview_paths: list[Path] = []
    stable_paths: list[Path] = []
    for root in roots:
        preview_paths += [
            root / "PowerShell" / "7-preview" / "pwsh.exe",
            root / "PowerShell" / "7-preview" / "preview" / "pwsh-preview.exe",
        ]
        stable_paths.append(root / "PowerShell" / "7" / "pwsh.exe")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        apps = Path(local) / "Microsoft" / "WindowsApps"
        preview_paths.append(apps / "pwsh-preview.exe")
        stable_paths.append(apps / "pwsh.exe")

    return (
        _which_exe("pwsh-preview")
        or _first_existing(preview_paths)
        or _which_exe("pwsh")
        or _first_existing(stable_paths)
    )


def _popen_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _fd_size(handle) -> int:
    try:
        return os.fstat(handle.fileno()).st_size
    except (OSError, ValueError):
        return 0


def _run_process(
    cmd,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    shell: bool,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[bool, bool, bool, str, str, int | None]:
    """Run a command; return (timed_out, overflow, cancelled, stdout, stderr, code).

    should_stop is polled while waiting so a user cancellation (ctrl+c) kills
    the command instead of waiting out the timeout. The child tree is always
    reaped on an exception, including KeyboardInterrupt.
    """
    # Use files instead of pipes: grandchildren that survive a kill cannot keep
    # us blocked waiting for EOF on an inherited pipe. Poll while waiting so a
    # runaway command that floods stdout is stopped instead of filling the disk.
    job = _WindowsJob() if os.name == "nt" else None
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=out_file,
            stderr=err_file,
            env=env,
            shell=shell,
            **_popen_group_kwargs(),
        )
        if job is not None:
            job.assign(proc)
        timed_out = False
        overflow = False
        cancelled = False
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    proc.wait(timeout=0.05)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if should_stop is not None and should_stop():
                    cancelled = True
                elif _fd_size(out_file) + _fd_size(err_file) > MAX_CAPTURE_BYTES:
                    overflow = True
                elif time.monotonic() >= deadline:
                    timed_out = True
                if not (cancelled or overflow or timed_out):
                    continue
                if job is not None:
                    job.terminate()
                _kill_process_tree(proc)
                break
        except BaseException:
            # Ctrl+C or any other error: never leave the child tree running.
            if job is not None:
                job.terminate()
            _kill_process_tree(proc)
            raise
        finally:
            if job is not None:
                job.close()
        out_file.seek(0)
        err_file.seek(0)
        stdout = out_file.read(MAX_CAPTURE_BYTES).decode("utf-8", "replace")
        stderr = err_file.read(MAX_CAPTURE_BYTES).decode("utf-8", "replace")
    return timed_out, overflow, cancelled, stdout, stderr, proc.returncode


def tool_pwsh(
    args: dict[str, Any],
    cwd: Path,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("command must be a non-empty string")
    try:
        timeout = int(args.get("timeout") or 120)
    except (TypeError, ValueError):
        timeout = 120
    timeout = max(1, min(timeout, MAX_COMMAND_SECONDS))

    pwsh = _find_pwsh()
    if not pwsh:
        raise ToolError(
            "PowerShell 7 (pwsh / pwsh-preview) not found. Install it with "
            "'winget install Microsoft.PowerShell', or set ELA_PWSH to "
            "the pwsh executable."
        )
    env = os.environ.copy()
    env.update(
        {
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONIOENCODING": "utf-8",
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        }
    )
    script = PWSH_PRELUDE + command + PWSH_EPILOGUE
    timed_out, overflow, cancelled, stdout, stderr, returncode = _run_process(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd,
        env,
        timeout,
        shell=False,
        should_stop=should_stop,
    )

    parts = []
    if cancelled:
        parts.append("Error: command cancelled.")
    if timed_out:
        parts.append(f"Error: command timed out after {timeout}s.")
    if overflow:
        parts.append(
            f"Error: command output exceeded "
            f"{MAX_CAPTURE_BYTES // (1024 * 1024)} MiB and was stopped; "
            "narrow the command or redirect its output."
        )
    if stdout.strip():
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append("[stderr]\n" + stderr.rstrip())
    if not timed_out and not overflow and not cancelled:
        parts.append(f"exit {returncode}")
    return _trim("\n".join(parts))


def _walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames if name.lower() not in SKIP_DIRS_LOWER
        ]
        for name in filenames:
            yield Path(dirpath) / name


@lru_cache(maxsize=4096)
def _glob_segments_match(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Match path segments; a lone ** spans any number of directories."""
    if not pattern:
        return not parts
    head = pattern[0]
    if head == "**":
        rest = pattern[1:]
        while rest and rest[0] == "**":
            rest = rest[1:]
        if not rest:
            return True
        for index in range(len(parts) + 1):
            if _glob_segments_match(parts[index:], rest):
                return True
        return False
    if not parts:
        return False
    if not fnmatch.fnmatch(parts[0], head):
        return False
    return _glob_segments_match(parts[1:], pattern[1:])


def _glob_match(relative: str, pattern: str) -> bool:
    """Shell-style glob where a single * never crosses a directory separator."""
    return _glob_segments_match(tuple(relative.split("/")), tuple(pattern.split("/")))


def _search_files(pattern: str, target: Path, cwd: Path) -> str:
    pattern = pattern.replace("\\", "/").strip()
    basename_only = "/" not in pattern
    matches: list[Path] = []
    scanned = 0
    for path in _walk_files(target):
        scanned += 1
        if scanned > 100_000:
            break
        if basename_only:
            hit = fnmatch.fnmatch(path.name, pattern)
        else:
            try:
                relative = path.relative_to(target).as_posix()
            except ValueError:
                continue
            hit = _glob_match(relative, pattern)
        if hit:
            matches.append(path)
            if len(matches) >= MAX_SEARCH_FILES:
                break
    if not matches:
        return "No files."
    out = sorted(_display_path(path, cwd) for path in matches)
    if len(matches) >= MAX_SEARCH_FILES:
        out.append(f"...[{MAX_SEARCH_FILES}+ files; narrow the glob]")
    return _trim("\n".join(out))


def _is_text(path: Path, limit: int = 2_000_000) -> bool:
    try:
        if path.stat().st_size > limit:
            return False
        with open(path, "rb") as handle:
            return b"\x00" not in handle.read(2048)
    except OSError:
        return False


def _display_path(path: Path, cwd: Path) -> str:
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()


def _format_counts(counts: list[tuple[int, str]]) -> str:
    if not counts:
        return "No matches."
    counts.sort(key=lambda item: (-item[0], item[1]))
    total = sum(count for count, _ in counts)
    shown = counts[:MAX_COUNT_ROWS]
    lines = [f"{count:>4}  {name}" for count, name in shown]
    if len(counts) > len(shown):
        lines.append(
            f"...[{len(counts) - len(shown)} more files; narrow the search]"
        )
    lines.append(f"total {total} matches in {len(counts)} files")
    return "\n".join(lines)


def _search_rg(pattern: str, target: Path, cwd: Path, mode: str) -> str | None:
    rg = shutil.which("rg")
    if not rg:
        return None
    cmd = [
        rg,
        "--no-heading",
        "--line-number",
        "--color",
        "never",
        "--with-filename",
        "--max-columns",
        "400",
        "--max-columns-preview",
    ]
    for skip in sorted(SKIP_DIRS):
        # Match the skipped directory at any depth, not just the search root.
        cmd += ["--glob", f"!**/{skip}/**"]
    if mode == "count":
        cmd.append("--count-matches")
    try:
        target_arg = target.relative_to(cwd).as_posix() or "."
    except ValueError:
        target_arg = str(target)
    cmd += ["-e", pattern, "--", target_arg]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode == 2:
        return "Error: " + (proc.stderr.strip() or "invalid search")
    if proc.returncode == 1 or not proc.stdout.strip():
        return "No matches."
    lines = proc.stdout.rstrip().splitlines()
    if mode == "count":
        counts: list[tuple[int, str]] = []
        for line in lines:
            name, _, raw_count = line.rpartition(":")
            if not name:
                continue
            try:
                counts.append((int(raw_count), name))
            except ValueError:
                continue
        return _trim(_format_counts(counts))
    out: list[str] = []
    for line in lines:
        out.append(line)
        if len(out) >= MAX_SEARCH_RESULTS:
            break
    if len(lines) > len(out):
        out.append(f"...[{len(lines) - len(out)} more; narrow the search]")
    return _trim("\n".join(out))


def _search_fallback(pattern: str, target: Path, cwd: Path, mode: str) -> str:
    if mode == "files":
        if target.is_file():
            raise ToolError("mode=files expects a directory path")
        try:
            return _search_files(pattern, target, cwd)
        except (ValueError, OSError) as exc:
            raise ToolError(f"invalid glob {pattern!r}: {exc}")

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regex {pattern!r}: {exc}")

    results: list[str] = []
    counts: list[tuple[int, str]] = []
    scanned = 0
    paths = [target] if target.is_file() else _walk_files(target)
    for path in paths:
        scanned += 1
        if scanned > 20_000:
            break
        if not _is_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if mode == "count":
            count = len(regex.findall(text))
            if count:
                counts.append((count, _display_path(path, cwd)))
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                line = line.strip()
                if len(line) > MAX_LINE_CHARS:
                    line = line[:MAX_LINE_CHARS] + "…"
                results.append(f"{_display_path(path, cwd)}:{number}:{line}")
                if len(results) >= MAX_SEARCH_RESULTS:
                    break
        if len(results) >= MAX_SEARCH_RESULTS:
            break

    if mode == "count":
        return _trim(_format_counts(counts))
    if not results:
        return "No matches."
    if len(results) >= MAX_SEARCH_RESULTS:
        results.append(f"...[{MAX_SEARCH_RESULTS}+ matches; narrow the search]")
    return _trim("\n".join(results))


def tool_search(args: dict[str, Any], cwd: Path) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("pattern must be a non-empty string")
    mode = args.get("mode") or "content"
    if mode not in ("content", "files", "count"):
        raise ToolError("mode must be content, files, or count")
    target = resolve_path(cwd, str(args.get("path") or "."))
    if not target.exists():
        raise ToolError(f"path not found: {_display_path(target, cwd)}")
    if mode == "files" and target.is_file():
        raise ToolError("mode=files expects a directory path")
    if mode in ("content", "count"):
        try:
            result = _search_rg(pattern, target, cwd, mode)
            if result is not None:
                return result
        except (subprocess.TimeoutExpired, OSError):
            pass
    return _search_fallback(pattern, target, cwd, mode)


TOOL_FUNCS = {
    "read": tool_read,
    "write": tool_write,
    "edit": tool_edit,
    "pwsh": tool_pwsh,
    "search": tool_search,
}


def execute_tool(
    name: str,
    args: dict[str, Any],
    cwd: Path,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    """Run a tool. All errors are converted to model-visible values."""
    func = TOOL_FUNCS.get(name)
    if func is None:
        return f"Error: unknown tool {name!r}. Available: {', '.join(TOOL_FUNCS)}"
    try:
        # pwsh is the only tool that can run for minutes; it polls cancellation.
        if name == "pwsh":
            return tool_pwsh(args, cwd, should_stop=should_stop)
        return func(args, cwd)
    except ToolError as exc:
        return f"Error: {exc}"
    except subprocess.TimeoutExpired:
        return "Error: command timed out."
    except OSError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # keep the loop alive on unexpected tool failures
        return f"Error: {type(exc).__name__}: {exc}"


def one_line(text: str, limit: int = 70) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_call(name: str, args: dict[str, Any]) -> str:
    """Short human-readable label for a tool call, shown in the terminal."""
    if name == "read":
        raw = args.get("paths", args.get("path", ""))
        if isinstance(raw, list):
            label = ", ".join(str(p) for p in raw[:2])
            if len(raw) > 2:
                label += f" +{len(raw) - 2}"
        else:
            label = str(raw)
        start, end = args.get("start"), args.get("end")
        if start or end:
            label += f" ({start or 1}-{end or '…'})"
        return one_line(label)
    if name == "pwsh":
        return one_line(args.get("command", ""))
    if name in ("write", "edit"):
        return one_line(args.get("path", ""))
    if name == "search":
        suffix = f" [{args.get('mode')}]" if args.get("mode") not in (None, "content") else ""
        return one_line(str(args.get("pattern", "")) + suffix)
    if name == "askuser":
        return one_line(args.get("question", ""))
    return one_line(str(args))


def result_headline(result: str, limit: int = 70) -> str:
    """Compact result summary for tool output blocks."""
    lines = [line for line in result.splitlines() if line.strip()]
    if not lines:
        return "(no output)"
    first = one_line(lines[0], limit)
    if len(lines) > 1:
        return f"{first}  +{len(lines) - 1} lines"
    return first
