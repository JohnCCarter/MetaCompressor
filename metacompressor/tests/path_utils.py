"""Test-only helpers for locating the repository root from any nested test path."""

from __future__ import annotations

from pathlib import Path


def repository_root(from_file: Path) -> Path:
    """Return the directory containing ``pyproject.toml`` above *from_file*."""
    here = from_file.resolve()
    for cand in (here, *here.parents):
        if (cand / "pyproject.toml").is_file():
            return cand
    msg = f"Could not locate repo root (pyproject.toml) above {here}"
    raise RuntimeError(msg)
