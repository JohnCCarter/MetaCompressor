"""Tests for the production validation benchmark script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_MODULE_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "production_validation.py"
_SPEC = importlib.util.spec_from_file_location("mc_production_validation", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Unable to load production_validation module")
production_validation = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = production_validation
_SPEC.loader.exec_module(production_validation)


def test_mode_verdict_thresholds():
    assert production_validation._mode_verdict(-15.0) == "strong win"
    assert production_validation._mode_verdict(-1.0) == "win"
    assert production_validation._mode_verdict(5.0) == "acceptable"
    assert production_validation._mode_verdict(15.0) == "loss"


def test_build_final_verdict_confirmed():
    results = [
        {
            "realism": "semi-realistic",
            "structured": True,
            "mc_summary": {"delta_vs_tar_zstd_pct": -12.0},
        },
        {
            "realism": "semi-realistic",
            "structured": True,
            "mc_summary": {"delta_vs_tar_zstd_pct": -11.0},
        },
        {
            "realism": "semi-realistic",
            "structured": True,
            "mc_summary": {"delta_vs_tar_zstd_pct": -10.5},
        },
    ]
    assert production_validation._build_final_verdict(results) == "PRODUCTION_EDGE_CONFIRMED"


def test_build_final_verdict_partial_on_structured_regression():
    results = [
        {
            "realism": "semi-realistic",
            "structured": True,
            "mc_summary": {"delta_vs_tar_zstd_pct": -12.0},
        },
        {
            "realism": "semi-realistic",
            "structured": True,
            "mc_summary": {"delta_vs_tar_zstd_pct": -11.0},
        },
        {
            "realism": "semi-realistic",
            "structured": True,
            "mc_summary": {"delta_vs_tar_zstd_pct": 12.0},
        },
    ]
    assert production_validation._build_final_verdict(results) == "PRODUCTION_EDGE_PARTIAL"


def test_run_validation_writes_reports_for_small_fixture(tmp_path, monkeypatch):
    def generate_small_corpus(root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.log").write_text(
            "2026-01-01T00:00:00Z level=INFO service=api request_id=1 path=/ping status=200\n"
            "2026-01-01T00:00:01Z level=INFO service=api request_id=2 path=/ping status=200\n"
            "2026-01-01T00:00:02Z level=ERROR service=api request_id=3 path=/ping status=500\n",
            encoding="utf-8",
        )
        (root / "events.ndjson").write_text(
            '{"service":"api","status":200,"request_id":"a"}\n'
            '{"service":"api","status":200,"request_id":"b"}\n',
            encoding="utf-8",
        )

    monkeypatch.setattr(
        production_validation,
        "_dataset_specs",
        lambda include_very_large: [
            production_validation.DatasetSpec(
                name="tiny_fixture",
                dataset_type="app/service logs",
                realism="semi-realistic",
                structured=True,
                generator=generate_small_corpus,
            )
        ],
    )
    monkeypatch.setattr(production_validation, "_brotli_available", lambda: False)

    payload = production_validation.run_validation(output_dir=tmp_path, include_very_large=False)

    assert payload["correctness_passed"] is True
    assert payload["determinism_passed"] is True
    assert (tmp_path / "metacompressor_production_validation.json").exists()
    assert (tmp_path / "metacompressor_production_validation.md").exists()
    assert (tmp_path / "metacompressor_structure_v2_report.md").exists()
