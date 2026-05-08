#!/usr/bin/env python3
"""Run pytest for pre-commit; prefer repo .venv so the package is importable."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    # Stress/hardening suites write Markdown under results/hardening/; redirect so pre-commit does not
    # see dirty tracked files (see metacompressor/tests/stress/test_stress_suite.py).
    if "METACOMPRESSOR_TEST_RESULTS_DIR" not in os.environ:
        os.environ["METACOMPRESSOR_TEST_RESULTS_DIR"] = tempfile.mkdtemp(
            prefix="mc_precommit_results_",
        )

    if sys.platform == "win32":
        venv_python = root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = root / ".venv" / "bin" / "python"

    exe = str(venv_python) if venv_python.is_file() else sys.executable
    # Avoid writing .pytest_cache (pre-commit treats any working-tree change as hook failure).
    cmd = [
        exe,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        str(root / "metacompressor" / "tests"),
        "-q",
        "--tb=no",
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
