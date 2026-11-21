"""Path resolution shared by tools and permissions.

Paths are interpreted natively. The shell is PowerShell 7, which resolves
``/tmp`` as ``<current drive>:\\tmp`` just like Python does, so no MSYS
translation is applied.
"""

from __future__ import annotations

from pathlib import Path


def resolve_path(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()
