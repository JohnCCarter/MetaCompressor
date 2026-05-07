from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "differential_hit_rate.py"
)
_SPEC = importlib.util.spec_from_file_location("mc_differential_hit_rate", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Unable to load differential_hit_rate module")
differential_hit_rate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = differential_hit_rate
_SPEC.loader.exec_module(differential_hit_rate)


def test_harness_writes_json_and_markdown(tmp_path: Path) -> None:
    payload = differential_hit_rate.run_harness(output_dir=tmp_path, run_count=2)
    assert payload["verification_mode_only"] is True
    assert (tmp_path / "differential_hit_rate.json").exists()
    assert (tmp_path / "differential_hit_rate.md").exists()


def test_harness_schema_contains_required_fields(tmp_path: Path) -> None:
    payload = differential_hit_rate.run_harness(output_dir=tmp_path, run_count=2)
    assert "workloads" in payload
    assert payload["workloads"]
    required = {
        "workload_class",
        "scenario",
        "run_count",
        "cache_hit_candidate_rate",
        "archives_equal_rate",
        "archives_equal_given_cache_hit_rate",
        "top_miss_reason",
        "detailed_miss_reason_counts",
        "reusable_but_not_hit_chunks_avg",
        "partial_reuse_opportunity_count",
        "estimated_benefit_if_partial_reuse_existed",
        "reuse_chunk_ratio_avg",
        "rescan_chunk_ratio_avg",
        "reuse_chunk_distribution",
        "rescan_chunk_distribution",
        "cache_miss_reason_counts",
        "total_time_ms_avg",
        "total_time_ms_stdev",
        "lossless_status",
        "determinism_status",
        "sample_size",
    }
    for row in payload["workloads"]:
        assert required.issubset(set(row.keys()))


def test_cache_return_not_enabled_or_required(tmp_path: Path) -> None:
    payload = differential_hit_rate.run_harness(output_dir=tmp_path, run_count=2)
    assert payload["verification_mode_only"] is True
    assert payload["cache_return_enabled"] is False
    assert "mutating_hit_rate_avg" in payload
    assert "mutating_archives_equal_given_hit_avg" in payload
