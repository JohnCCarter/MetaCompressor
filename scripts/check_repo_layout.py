#!/usr/bin/env python3
"""Fail if tracked Python files sit outside allowed layout (see docs/repository-layout-policy.md)."""

from __future__ import annotations

import subprocess
import sys


def git_ls_py_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        capture_output=True,
        text=False,
        check=True,
    ).stdout
    if not out:
        return []
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def is_allowed(path: str) -> bool:
    p = path.replace("\\", "/")

    if p.startswith("benchmarks/"):
        return True
    if p.startswith("scripts/"):
        return True
    if p.startswith("metacompressor/tests/"):
        return True

    if not p.startswith("metacompressor/") or not p.endswith(".py"):
        return False

    rel = p[len("metacompressor/") :]
    if "/" in rel:
        return False
    base = rel.rsplit("/", 1)[-1]
    if base.startswith("test_"):
        return False
    return True


def main() -> int:
    bad: list[str] = []
    for path in sorted(git_ls_py_files()):
        if not is_allowed(path):
            bad.append(path)

    if bad:
        print(
            "Layout check failed. These tracked .py files are not in an allowed location:",
            file=sys.stderr,
        )
        for p in bad:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nAllowed:\n"
            "  - metacompressor/<module>.py  (flat package; not test_*.py)\n"
            "  - metacompressor/tests/**.py\n"
            "  - benchmarks/**\n"
            "  - scripts/**\n"
            "\nSee docs/repository-layout-policy.md and docs/adr/README.md.",
            file=sys.stderr,
        )
        return 1

    print("Layout check OK (%d tracked Python files)." % len(git_ls_py_files()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
