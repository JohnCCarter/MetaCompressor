from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from metacompressor.tests.path_utils import repository_root

_MODULE_PATH = (
    repository_root(Path(__file__))
    / "benchmarks"
    / "differential"
    / "differential_parity_gate.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "mc_differential_parity_gate", _MODULE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Unable to load differential_parity_gate module")
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)


def test_parity_gate_writes_outputs(tmp_path: Path) -> None:
    payload = gate.run_harness(output_dir=tmp_path, run_count=4)
    assert payload["runtime_experimental"] is True
    assert (tmp_path / "differential_parity_gate.json").exists()
    assert (tmp_path / "differential_parity_gate.md").exists()


def test_parity_gate_schema_has_required_fields(tmp_path: Path) -> None:
    payload = gate.run_harness(output_dir=tmp_path, run_count=4)
    required = {
        "byte_identical_parity_rate",
        "strategy_encoding_match_rate",
        "deterministic_merge_status",
        "fallback_reason_counts",
        "noisy_fail_closed_status",
        "verification_mode",
        "returned_archive_source",
        "real_decision_metadata_used",
        "runtime_substitution_used_rate",
        "runtime_substitution_fail_reason_counts",
        "mismatch_stage_counts",
        "suspected_global_dependency_rate",
        "workloads",
    }
    assert required.issubset(set(payload.keys()))
    assert payload["workloads"]
