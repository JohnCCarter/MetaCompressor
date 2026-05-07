from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "differential_partial_reuse_simulation.py"
)
_SPEC = importlib.util.spec_from_file_location("mc_partial_reuse_sim", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Unable to load differential_partial_reuse_simulation module")
sim = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = sim
_SPEC.loader.exec_module(sim)


def test_simulation_harness_writes_outputs(tmp_path: Path) -> None:
    payload = sim.run_harness(output_dir=tmp_path, run_count=3)
    assert payload["runtime_experimental"] is True
    assert (tmp_path / "differential_partial_reuse_simulation.json").exists()
    assert (tmp_path / "differential_partial_reuse_simulation.md").exists()


def test_simulation_schema_contains_required_fields(tmp_path: Path) -> None:
    payload = sim.run_harness(output_dir=tmp_path, run_count=3)
    assert payload["workloads"]
    required = {
        "workload_class",
        "run_count",
        "full_rebuild_time_ms_avg",
        "full_rebuild_time_ms_stdev",
        "estimated_partial_reuse_saved_chunks",
        "estimated_partial_reuse_saved_bytes",
        "estimated_partial_reuse_saved_time_ms",
        "estimated_partial_reuse_build_fraction",
        "estimated_partial_reuse_speedup_pct",
        "real_decision_metadata_used",
        "runtime_substitution_used_rate",
        "runtime_substitution_fallback_rate",
        "runtime_substitution_time_ms_avg",
        "sample_size",
    }
    for row in payload["workloads"]:
        assert required.issubset(set(row.keys()))
