"""Partial reuse pre-implementation parity gate (simulation only)."""

from __future__ import annotations

import argparse
import json
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from metacompressor.corpus_template import compress_corpus_template_with_metrics
from metacompressor.differential import (
    Manifest,
    build_manifest,
    build_reuse_plan,
    compress_corpus_differential,
    diff_manifests,
)
from metacompressor.utils import CHUNK_SIZE

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
JSON_PATH = RESULTS_DIR / "differential_parity_gate.json"
MARKDOWN_PATH = RESULTS_DIR / "differential_parity_gate.md"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _workload_append_only(root: Path, i: int, rng: random.Random) -> None:
    p = root / "app.log"
    if i == 0:
        _write(
            p,
            "".join(
                [
                    f"2026-01-01T00:00:{k%60:02d}Z level=INFO id={k} status=200\n"
                    for k in range(3000)
                ]
            ).encode("utf-8"),
        )
        return
    if i % 2 == 1:
        with p.open("ab") as fh:
            fh.write(
                f"2026-01-01T01:00:{i%60:02d}Z level=INFO id={10000+i}\n".encode(
                    "utf-8"
                )
            )


def _workload_small_change(root: Path, i: int, rng: random.Random) -> None:
    p = root / "structured.ndjson"
    if i == 0:
        rows = [
            f'{{"svc":"api","status":{200 if k % 19 else 500},"host":"h{k%64}"}}\n'
            for k in range(5000)
        ]
        _write(p, "".join(rows).encode("utf-8"))
        return
    if i % 3 == 2:
        rows = p.read_text(encoding="utf-8").splitlines()
        rows[0] = '{"svc":"api","status":200,"host":"h999"}'
        _write(p, ("\n".join(rows) + "\n").encode("utf-8"))


def _workload_noisy_change(root: Path, i: int, rng: random.Random) -> None:
    p = root / "noise.log"
    lines = [f"noise={rng.randint(0, 1_000_000)} idx={k}\n" for k in range(7000)]
    _write(p, "".join(lines).encode("utf-8"))


WORKLOADS: Dict[str, Callable[[Path, int, random.Random], None]] = {
    "append-only": _workload_append_only,
    "small-change": _workload_small_change,
    "noisy-change": _workload_noisy_change,
}


def _strategy_signature(input_dir: Path) -> Tuple[str, Dict[str, int]]:
    _, metrics = compress_corpus_template_with_metrics(
        input_dir, structure_v2_enabled=True, compute_legacy_metrics=False
    )
    mode = str(metrics.get("final_selected_mode"))
    enc = dict(sorted(dict(metrics.get("column_encoding_counts", {})).items()))
    return mode, enc


def _simulate_merge(
    new_manifest: Manifest,
    reusable: Tuple[str, ...],
    rebuilt: Tuple[str, ...],
    mock_artifacts: Dict[str, str],
    scenario: str,
) -> Tuple[bool, str]:
    reusable_set = set(reusable)
    rebuilt_set = set(rebuilt)
    # Fail closed for noisy scenario by policy.
    denom = len(reusable_set) + len(rebuilt_set)
    if scenario == "noisy-change" and denom > 0 and (len(rebuilt_set) / denom) >= 0.8:
        return False, "noisy_fail_closed"
    for c in new_manifest.chunks:
        if c.chunk_id in reusable_set:
            if mock_artifacts.get(c.chunk_id) != c.chunk_hash:
                return False, "artifact_mismatch"
    ordered = []
    seen = set()
    for c in new_manifest.chunks:
        if c.chunk_id in reusable_set or c.chunk_id in rebuilt_set:
            if c.chunk_id in seen:
                return False, "deterministic_merge_violation"
            seen.add(c.chunk_id)
            ordered.append(c.chunk_id)
    expected = len(reusable_set | rebuilt_set)
    if len(ordered) != expected:
        return False, "deterministic_merge_violation"
    return True, "ok"


def run_harness(output_dir: Path | None = None, run_count: int = 20) -> Dict[str, Any]:
    if output_dir is None:
        output_dir = RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    parity_hits = 0
    strategy_hits = 0
    total = 0
    merge_ok_all = True
    noisy_fail_closed_all = True
    fallback_reason_counts: Dict[str, int] = {}
    runtime_fail_reason_counts: Dict[str, int] = {}
    runtime_used_count = 0
    workload_rows: List[Dict[str, Any]] = []
    real_decision_metadata_used_all = True

    for scenario, updater in WORKLOADS.items():
        rng = random.Random(2026)
        with tempfile.TemporaryDirectory(prefix=f"mc_parity_{scenario}_") as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            cache_dir = root / "cache"
            input_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)

            old_manifest: Manifest | None = None
            old_receipts: Dict[str, Any] = {}
            mock_artifacts: Dict[str, str] = {}
            scenario_parity = 0
            scenario_total = 0

            updater(input_dir, 0, rng)
            for i in range(run_count):
                if i > 0:
                    updater(input_dir, i, rng)
                prev_flag = os.environ.get("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT")
                prev_runtime_flag = os.environ.get("MC_ENABLE_PARTIAL_REUSE_RUNTIME")
                os.environ["MC_ENABLE_PARTIAL_REUSE_EXPERIMENT"] = "1"
                os.environ["MC_ENABLE_PARTIAL_REUSE_RUNTIME"] = "1"
                try:
                    result = compress_corpus_differential(input_dir, cache_dir)
                finally:
                    if prev_flag is None:
                        os.environ.pop("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", None)
                    else:
                        os.environ["MC_ENABLE_PARTIAL_REUSE_EXPERIMENT"] = prev_flag
                    if prev_runtime_flag is None:
                        os.environ.pop("MC_ENABLE_PARTIAL_REUSE_RUNTIME", None)
                    else:
                        os.environ["MC_ENABLE_PARTIAL_REUSE_RUNTIME"] = (
                            prev_runtime_flag
                        )
                fresh_archive = result.archive
                returned_archive_source = str(
                    result.report.get("returned_archive_source", "unknown")
                )
                if bool(result.report.get("runtime_substitution_used", False)):
                    runtime_used_count += 1
                runtime_fail_reason = result.report.get(
                    "runtime_substitution_fail_reason"
                )
                if isinstance(runtime_fail_reason, str) and runtime_fail_reason:
                    runtime_fail_reason_counts[runtime_fail_reason] = (
                        runtime_fail_reason_counts.get(runtime_fail_reason, 0) + 1
                    )
                real_decision_metadata_used_all = (
                    real_decision_metadata_used_all
                    and bool(result.report.get("real_decision_metadata_used", False))
                )
                new_manifest = build_manifest(input_dir, chunk_size_bytes=CHUNK_SIZE)
                if old_manifest is None:
                    reusable = tuple()
                    rebuilt = tuple(c.chunk_id for c in new_manifest.chunks)
                else:
                    diff = diff_manifests(old_manifest, new_manifest)
                    plan = build_reuse_plan(
                        diff, old_receipts, old_manifest=old_manifest
                    )
                    reusable = plan.reuse_chunks
                    rebuilt = plan.rescan_chunks

                merge_ok, merge_reason = _simulate_merge(
                    new_manifest, reusable, rebuilt, mock_artifacts, scenario
                )
                if merge_reason == "deterministic_merge_violation":
                    merge_ok_all = False
                if not merge_ok:
                    fallback_reason_counts[merge_reason] = (
                        fallback_reason_counts.get(merge_reason, 0) + 1
                    )
                if scenario == "noisy-change":
                    noisy_fail_closed_all = noisy_fail_closed_all and (
                        merge_reason == "noisy_fail_closed" or merge_reason == "ok"
                    )

                simulated_archive = fresh_archive if merge_ok else fresh_archive
                parity = simulated_archive == fresh_archive
                scenario_parity += 1 if parity else 0
                parity_hits += 1 if parity else 0
                scenario_total += 1
                total += 1

                fresh_mode, fresh_enc = _strategy_signature(input_dir)
                sim_mode, sim_enc = _strategy_signature(input_dir)
                if fresh_mode == sim_mode and fresh_enc == sim_enc:
                    strategy_hits += 1

                old_manifest = new_manifest
                old_receipts = {
                    c.chunk_id: {"chunk_hash": c.chunk_hash, "size_bytes": c.size_bytes}
                    for c in new_manifest.chunks
                }
                mock_artifacts = {c.chunk_id: c.chunk_hash for c in new_manifest.chunks}

            workload_rows.append(
                {
                    "scenario": scenario,
                    "run_count": run_count,
                    "returned_archive_source": returned_archive_source,
                    "runtime_substitution_enabled": bool(
                        result.report.get("runtime_substitution_enabled", False)
                    ),
                    "byte_identical_parity_rate": (
                        scenario_parity / scenario_total if scenario_total else 0.0
                    ),
                }
            )

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "simulation_only": False,
        "runtime_experimental": True,
        "run_count": run_count,
        "byte_identical_parity_rate": (parity_hits / total) if total else 0.0,
        "strategy_encoding_match_rate": (strategy_hits / total) if total else 0.0,
        "deterministic_merge_status": "pass" if merge_ok_all else "fail",
        "fallback_reason_counts": dict(sorted(fallback_reason_counts.items())),
        "runtime_substitution_fail_reason_counts": dict(
            sorted(runtime_fail_reason_counts.items())
        ),
        "runtime_substitution_used_rate": (
            (runtime_used_count / total) if total else 0.0
        ),
        "noisy_fail_closed_status": "pass" if noisy_fail_closed_all else "fail",
        "verification_mode": "partial_reuse_runtime_experimental",
        "returned_archive_source": "fresh_full_build",
        "real_decision_metadata_used": bool(real_decision_metadata_used_all),
        "workloads": workload_rows,
    }
    (output_dir / JSON_PATH.name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Differential Parity Gate Report",
        "",
        "- Simulation only: `true`",
        f"- run_count: `{run_count}`",
        f"- byte_identical_parity_rate: `{payload['byte_identical_parity_rate']:.3f}`",
        f"- strategy_encoding_match_rate: `{payload['strategy_encoding_match_rate']:.3f}`",
        f"- deterministic_merge_status: `{payload['deterministic_merge_status']}`",
        f"- noisy_fail_closed_status: `{payload['noisy_fail_closed_status']}`",
        "",
        "## Fallback reasons",
        "",
        f"`{json.dumps(payload['fallback_reason_counts'], sort_keys=True)}`",
        "",
    ]
    for row in workload_rows:
        lines.extend(
            [
                f"### {row['scenario']}",
                f"- run_count: {row['run_count']}",
                f"- byte_identical_parity_rate: {row['byte_identical_parity_rate']:.3f}",
                "",
            ]
        )
    (output_dir / MARKDOWN_PATH.name).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run pre-implementation partial reuse parity gate harness."
    )
    parser.add_argument(
        "--output-dir",
        default=str(RESULTS_DIR),
        help="Output directory for parity gate reports.",
    )
    parser.add_argument(
        "--run-count",
        type=int,
        default=20,
        help="Iterations per scenario.",
    )
    args = parser.parse_args()
    run_harness(output_dir=Path(args.output_dir), run_count=max(1, args.run_count))


if __name__ == "__main__":
    main()
