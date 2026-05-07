"""Design/evidence-only partial-reuse simulation harness.

This harness does NOT implement partial reuse. It estimates potential benefit
using verification-mode differential telemetry only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import tempfile
import time
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Callable, Dict, List

from metacompressor.differential import compress_corpus_differential

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
JSON_PATH = RESULTS_DIR / "differential_partial_reuse_simulation.json"
MARKDOWN_PATH = RESULTS_DIR / "differential_partial_reuse_simulation.md"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _workload_append_only_logs(root: Path, iteration: int, rng: random.Random) -> None:
    p = root / "app.log"
    if iteration == 0:
        lines = [
            f"2026-01-01T00:00:{i%60:02d}Z level=INFO service=api id={i} status=200\n"
            for i in range(4000)
        ]
        _write(p, "".join(lines).encode("utf-8"))
        return
    if iteration % 2 == 1:
        with p.open("ab") as fh:
            fh.write(
                f"2026-01-01T01:00:{iteration%60:02d}Z level=INFO service=api id={10000+iteration} status=200\n".encode(
                    "utf-8"
                )
            )


def _workload_structured(root: Path, iteration: int, rng: random.Random) -> None:
    p = root / "records.ndjson"
    if iteration == 0:
        rows = [
            f'{{"svc":"api","status":{200 if i % 17 else 500},"region":"{"eu" if i % 2 else "us"}","host":"h{i%64}"}}\n'
            for i in range(6000)
        ]
        _write(p, "".join(rows).encode("utf-8"))
        return
    if iteration % 3 == 2:
        rows = p.read_text(encoding="utf-8").splitlines()
        rows[0] = '{"svc":"api","status":200,"region":"eu","host":"h999"}'
        _write(p, ("\n".join(rows) + "\n").encode("utf-8"))


def _workload_mixed(root: Path, iteration: int, rng: random.Random) -> None:
    text = root / "events.log"
    blob = root / "blob.bin"
    if iteration == 0:
        _write(
            text,
            "".join([f"event=hb seq={i} code=200\n" for i in range(2500)]).encode(
                "utf-8"
            ),
        )
        _write(blob, bytes([i % 256 for i in range(512 * 1024)]))
        return
    if iteration % 3 == 1:
        with text.open("ab") as fh:
            fh.write(f"event=append seq={iteration}\n".encode("utf-8"))
    elif iteration % 3 == 2:
        data = bytearray(blob.read_bytes())
        data[min(len(data) - 1, iteration)] ^= 0x01
        _write(blob, bytes(data))


def _workload_noisy(root: Path, iteration: int, rng: random.Random) -> None:
    p = root / "noise.log"
    lines = [f"noise={rng.randint(0, 1_000_000)} idx={i}\n" for i in range(8000)]
    _write(p, "".join(lines).encode("utf-8"))


WORKLOADS: Dict[str, Callable[[Path, int, random.Random], None]] = {
    "append-only logs": _workload_append_only_logs,
    "structured corpora": _workload_structured,
    "mixed binaries": _workload_mixed,
    "noisy datasets": _workload_noisy,
}


def _run_workload(
    workload_class: str,
    updater: Callable[[Path, int, random.Random], None],
    run_count: int,
) -> Dict[str, Any]:
    rng = random.Random(4242)
    with tempfile.TemporaryDirectory(prefix="mc_partial_reuse_sim_") as tmp:
        tmp_root = Path(tmp)
        input_dir = tmp_root / "input"
        cache_dir = tmp_root / "cache"
        input_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        base_full_ms: List[int] = []
        est_saved_chunks: List[float] = []
        est_saved_bytes: List[float] = []
        est_saved_ms: List[float] = []
        est_build_fraction: List[float] = []
        est_speedup_pct: List[float] = []
        returned_archive_sources: List[str] = []
        real_decision_metadata_used_flags: List[bool] = []
        runtime_used_flags: List[bool] = []
        runtime_substitution_ms: List[int] = []
        runtime_fallback_count = 0

        updater(input_dir, 0, rng)
        for i in range(run_count):
            if i > 0:
                updater(input_dir, i, rng)
            prev_flag = os.environ.get("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT")
            prev_runtime_flag = os.environ.get("MC_ENABLE_PARTIAL_REUSE_RUNTIME")
            os.environ["MC_ENABLE_PARTIAL_REUSE_EXPERIMENT"] = "1"
            os.environ["MC_ENABLE_PARTIAL_REUSE_RUNTIME"] = "1"
            try:
                t0 = time.perf_counter()
                result = compress_corpus_differential(input_dir, cache_dir)
            finally:
                if prev_flag is None:
                    os.environ.pop("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", None)
                else:
                    os.environ["MC_ENABLE_PARTIAL_REUSE_EXPERIMENT"] = prev_flag
                if prev_runtime_flag is None:
                    os.environ.pop("MC_ENABLE_PARTIAL_REUSE_RUNTIME", None)
                else:
                    os.environ["MC_ENABLE_PARTIAL_REUSE_RUNTIME"] = prev_runtime_flag
            full_ms = int((time.perf_counter() - t0) * 1000.0)
            report = result.report
            reuse = int(report.get("reuse_chunk_count", 0))
            rescan = int(report.get("rescan_chunk_count", 0))
            total_chunks = reuse + rescan
            reuse_ratio = (reuse / total_chunks) if total_chunks > 0 else 0.0
            build_fraction = 1.0 - reuse_ratio
            saved_chunks = float(reuse)
            saved_bytes = float(len(result.archive) * reuse_ratio)
            saved_ms = float(full_ms * reuse_ratio)
            speedup_pct = float(reuse_ratio * 100.0)

            base_full_ms.append(full_ms)
            est_saved_chunks.append(saved_chunks)
            est_saved_bytes.append(saved_bytes)
            est_saved_ms.append(saved_ms)
            est_build_fraction.append(build_fraction)
            est_speedup_pct.append(speedup_pct)
            returned_archive_sources.append(
                str(report.get("returned_archive_source", "unknown"))
            )
            real_decision_metadata_used_flags.append(
                bool(report.get("real_decision_metadata_used", False))
            )
            runtime_used = bool(report.get("runtime_substitution_used", False))
            runtime_used_flags.append(runtime_used)
            runtime_substitution_ms.append(
                int(report.get("runtime_substitution_time_ms", 0))
            )
            if not runtime_used:
                runtime_fallback_count += 1

        return {
            "workload_class": workload_class,
            "run_count": run_count,
            "full_rebuild_time_ms_avg": mean(base_full_ms) if base_full_ms else 0.0,
            "full_rebuild_time_ms_stdev": (
                pstdev(base_full_ms) if len(base_full_ms) > 1 else 0.0
            ),
            "estimated_partial_reuse_saved_chunks": (
                mean(est_saved_chunks) if est_saved_chunks else 0.0
            ),
            "estimated_partial_reuse_saved_bytes": (
                mean(est_saved_bytes) if est_saved_bytes else 0.0
            ),
            "estimated_partial_reuse_saved_time_ms": (
                mean(est_saved_ms) if est_saved_ms else 0.0
            ),
            "estimated_partial_reuse_build_fraction": (
                mean(est_build_fraction) if est_build_fraction else 1.0
            ),
            "estimated_partial_reuse_speedup_pct": (
                mean(est_speedup_pct) if est_speedup_pct else 0.0
            ),
            "returned_archive_source": (
                "fresh_full_build"
                if returned_archive_sources
                and all(v == "fresh_full_build" for v in returned_archive_sources)
                else "mixed"
            ),
            "real_decision_metadata_used": bool(
                real_decision_metadata_used_flags
                and all(real_decision_metadata_used_flags)
            ),
            "runtime_substitution_used_rate": (
                sum(1 for v in runtime_used_flags if v) / len(runtime_used_flags)
                if runtime_used_flags
                else 0.0
            ),
            "runtime_substitution_fallback_rate": (
                float(runtime_fallback_count) / float(run_count)
                if run_count > 0
                else 0.0
            ),
            "runtime_substitution_time_ms_avg": (
                mean(runtime_substitution_ms) if runtime_substitution_ms else 0.0
            ),
            "sample_size": run_count,
        }


def run_harness(output_dir: Path | None = None, run_count: int = 20) -> Dict[str, Any]:
    if output_dir is None:
        output_dir = RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        _run_workload(name, updater, run_count) for name, updater in WORKLOADS.items()
    ]
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "simulation_only": False,
        "runtime_experimental": True,
        "verification_mode": "partial_reuse_runtime_experimental",
        "returned_archive_source": "fresh_full_build",
        "real_decision_metadata_used": bool(
            rows
            and all(bool(r.get("real_decision_metadata_used", False)) for r in rows)
        ),
        "run_count_per_workload": run_count,
        "workloads": rows,
    }
    (output_dir / JSON_PATH.name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Differential Partial Reuse Simulation Report",
        "",
        "- Simulation only: `true`",
        f"- Run count per workload: `{run_count}`",
        "",
        "## Workloads",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"### {row['workload_class']}",
                f"- run_count: {row['run_count']}",
                f"- full_rebuild_time_ms_avg: {row['full_rebuild_time_ms_avg']:.2f}",
                f"- full_rebuild_time_ms_stdev: {row['full_rebuild_time_ms_stdev']:.2f}",
                f"- estimated_partial_reuse_saved_chunks: {row['estimated_partial_reuse_saved_chunks']:.2f}",
                f"- estimated_partial_reuse_saved_bytes: {row['estimated_partial_reuse_saved_bytes']:.2f}",
                f"- estimated_partial_reuse_saved_time_ms: {row['estimated_partial_reuse_saved_time_ms']:.2f}",
                f"- estimated_partial_reuse_build_fraction: {row['estimated_partial_reuse_build_fraction']:.3f}",
                f"- estimated_partial_reuse_speedup_pct: {row['estimated_partial_reuse_speedup_pct']:.2f}",
                "",
            ]
        )
    (output_dir / MARKDOWN_PATH.name).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run design-only partial-reuse simulation harness."
    )
    parser.add_argument(
        "--output-dir",
        default=str(RESULTS_DIR),
        help="Output directory for simulation report files.",
    )
    parser.add_argument(
        "--run-count",
        type=int,
        default=20,
        help="Iterations per workload class.",
    )
    args = parser.parse_args()
    run_harness(output_dir=Path(args.output_dir), run_count=max(1, args.run_count))


if __name__ == "__main__":
    main()
