"""Tests for the acceptance hardening benchmark script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "acceptance_hardening.py"
)
_SPEC = importlib.util.spec_from_file_location("mc_acceptance_hardening", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Unable to load acceptance_hardening module")
acceptance_hardening = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = acceptance_hardening
_SPEC.loader.exec_module(acceptance_hardening)


def _generate_small_corpus_for_validation(root: Path) -> None:
    """Small on-disk corpus for the validation smoke test."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.log").write_text(
        "2026-01-01T00:00:00Z level=INFO service=api request_id=1 path=/ping status=200\n"
        "2026-01-01T00:00:01Z level=INFO service=api request_id=2 path=/ping status=200\n"
        "2026-01-01T00:00:02Z level=ERROR service=api request_id=3 path=/ping status=500\n",
        encoding="utf-8",
    )


def _run_dataset_with_timeout_inprocess(tmp_root: Path, spec, timeout_s: int):
    """Run dataset measurement in-process (benchmark module is importlib-loaded; Windows spawn cannot re-import it)."""
    dataset_dir = tmp_root / "datasets" / spec.name
    work_dir = tmp_root / "work" / spec.name
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        acceptance_hardening._build_dataset(dataset_dir, spec)
        measured = acceptance_hardening._measure_dataset(dataset_dir, spec, work_dir)
        return acceptance_hardening._finalize_dataset_result(measured)
    except acceptance_hardening.ValidationError as exc:
        raise acceptance_hardening.ValidationError(str(exc)) from exc
    except Exception as exc:
        raise RuntimeError("dataset %s failed: %s" % (spec.name, exc)) from exc


def test_large_tests_gate_matches_exact_one(monkeypatch):
    monkeypatch.setenv("RUN_LARGE_TESTS", "1")
    assert acceptance_hardening._large_tests_enabled() is True
    monkeypatch.setenv("RUN_LARGE_TESTS", "true")
    assert acceptance_hardening._large_tests_enabled() is False


def test_run_validation_writes_reports_for_small_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        acceptance_hardening,
        "_run_dataset_with_timeout",
        _run_dataset_with_timeout_inprocess,
    )
    monkeypatch.setattr(
        acceptance_hardening,
        "_dataset_specs",
        lambda include_500mb: [
            acceptance_hardening.DatasetSpec(
                name="tiny_fixture",
                dataset_type="app/service logs",
                realism="semi-realistic",
                structured=True,
                generator=_generate_small_corpus_for_validation,
            )
        ],
    )

    payload = acceptance_hardening.run_validation(
        output_dir=tmp_path, include_500mb=False
    )

    assert payload["correctness_passed"] is True
    assert payload["determinism_passed"] is True
    assert (tmp_path / "metacompressor_acceptance_hardening.json").exists()
    markdown = (tmp_path / "metacompressor_acceptance_hardening.md").read_text(
        encoding="utf-8"
    )
    assert "## Win-rate summary" in markdown
    assert "Win-rate scope" in markdown
    assert "## Speed/memory summary" in markdown
    assert "## Trust/correctness summary" in markdown
    assert "## Remaining weak zones" in markdown
    assert "## Recommended next improvement" in markdown
