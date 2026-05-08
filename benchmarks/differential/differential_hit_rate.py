"""Verification-mode differential hit-rate harness."""

from __future__ import annotations

import argparse
import json
import random
import shutil
import tempfile
import time
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Callable, Dict, List

from metacompressor.corpus import decompress_corpus
from metacompressor.differential import compress_corpus_differential

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results" / "differential"
JSON_PATH = RESULTS_DIR / "differential_hit_rate.json"
MARKDOWN_PATH = RESULTS_DIR / "differential_hit_rate.md"


def _equal_dirs(a: Path, b: Path) -> bool:
    a_files = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    if a_files != b_files:
        return False
    for rel in a_files:
        if (a / rel).read_bytes() != (b / rel).read_bytes():
            return False
    return True


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _workload_append_only_logs(root: Path, iteration: int, rng: random.Random) -> None:
    log_path = root / "app.log"
    if iteration == 0:
        lines = [
            f"2026-01-01T00:00:{i%60:02d}Z level=INFO service=api id={i} status=200\n"
            for i in range(4000)
        ]
        _write(log_path, "".join(lines).encode("utf-8"))
        return
    if iteration % 2 == 1:
        with log_path.open("ab") as fh:
            fh.write(
                (
                    f"2026-01-01T00:00:{iteration:02d}Z level=INFO service=api id={1000+iteration} status=200\n"
                ).encode("utf-8")
            )


def _workload_structured_corpora(
    root: Path, iteration: int, rng: random.Random
) -> None:
    p = root / "records.ndjson"
    if iteration == 0:
        rows = [
            f'{{"service":"api","status":{200 if i % 19 else 500},"region":"{"eu" if i % 2 else "us"}","host":"h{i%64}"}}\n'
            for i in range(6000)
        ]
        _write(p, "".join(rows).encode("utf-8"))
        return
    if iteration % 3 == 2:
        rows = p.read_text(encoding="utf-8").splitlines()
        rows[0] = '{"service":"api","status":200,"region":"eu","host":"a9"}'
        _write(p, ("\n".join(rows) + "\n").encode("utf-8"))


def _workload_mixed_binaries(root: Path, iteration: int, rng: random.Random) -> None:
    text = root / "events.log"
    blob = root / "blob.bin"
    if iteration == 0:
        text_lines = [f"event=heartbeat seq={i} code=200\n" for i in range(2500)]
        _write(text, "".join(text_lines).encode("utf-8"))
        _write(blob, bytes([i % 256 for i in range(512 * 1024)]))
        return
    if iteration % 3 == 1:
        with text.open("ab") as fh:
            fh.write(f"event=heartbeat seq={iteration}\n".encode("utf-8"))
    elif iteration % 3 == 2:
        data = bytearray(blob.read_bytes())
        data[min(len(data) - 1, iteration)] ^= 0x01
        _write(blob, bytes(data))


def _workload_noisy(root: Path, iteration: int, rng: random.Random) -> None:
    p = root / "noise.log"
    if iteration == 0:
        lines = [f"noise={rng.randint(0, 1_000_000)} idx={i}\n" for i in range(8000)]
        _write(p, "".join(lines).encode("utf-8"))
        return
    lines = [f"noise={rng.randint(0, 1_000_000)} idx={i}\n" for i in range(8000)]
    _write(p, "".join(lines).encode("utf-8"))


def _scenario_unchanged(_: Path, __: int, ___: random.Random) -> None:
    return


SCENARIOS: Dict[str, Callable[[Path, int, random.Random], None]] = {
    "unchanged": _scenario_unchanged,
    "append-only": lambda root, i, rng: _workload_append_only_logs(root, i, rng),
    "small-change": lambda root, i, rng: _workload_structured_corpora(root, i, rng),
    "noisy-change": lambda root, i, rng: _workload_noisy(root, i, rng),
}

WORKLOADS: Dict[str, Callable[[Path, int, random.Random], None]] = {
    "append-only logs": _workload_append_only_logs,
    "structured corpora": _workload_structured_corpora,
    "mixed binaries": _workload_mixed_binaries,
    "noisy datasets": _workload_noisy,
}


def _run_workload(
    workload_class: str,
    scenario: str,
    updater: Callable[[Path, int, random.Random], None],
    run_count: int,
) -> Dict[str, Any]:
    rng = random.Random(1337)
    with tempfile.TemporaryDirectory(prefix="mc_diff_hit_rate_") as tmp:
        tmp_root = Path(tmp)
        input_dir = tmp_root / "input"
        cache_dir = tmp_root / "cache"
        input_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        hits = 0
        equals = 0
        miss_reason_counts: Dict[str, int] = {}
        detailed_miss_reason_counts: Dict[str, int] = {}
        reuse_ratios: List[float] = []
        rescan_ratios: List[float] = []
        total_times_ms: List[int] = []
        reuse_chunk_values: List[int] = []
        rescan_chunk_values: List[int] = []
        reusable_but_not_hit_values: List[int] = []
        partial_reuse_opportunity_count = 0
        lossless_ok = True
        determinism_ok = True

        updater(input_dir, 0, rng)
        for i in range(run_count):
            if i > 0:
                updater(input_dir, i, rng)

            t0 = time.perf_counter()
            result = compress_corpus_differential(input_dir, cache_dir)
            total_times_ms.append(int((time.perf_counter() - t0) * 1000.0))

            report = result.report
            if bool(report.get("cache_hit_candidate", False)):
                hits += 1
            else:
                reason = str(report.get("reason", "unknown"))
                miss_reason_counts[reason] = miss_reason_counts.get(reason, 0) + 1
                for k, v in dict(report.get("miss_reasons", {})).items():
                    detailed_miss_reason_counts[k] = detailed_miss_reason_counts.get(
                        k, 0
                    ) + int(v)
                reusable_but_not_hit = int(report.get("reusable_but_not_hit_chunks", 0))
                reusable_but_not_hit_values.append(reusable_but_not_hit)
                if bool(report.get("partial_reuse_opportunity", False)):
                    partial_reuse_opportunity_count += 1
            if report.get("archives_equal") is True:
                equals += 1

            reuse = int(report.get("reuse_chunk_count", 0))
            rescan = int(report.get("rescan_chunk_count", 0))
            reuse_chunk_values.append(reuse)
            rescan_chunk_values.append(rescan)
            denom = reuse + rescan
            reuse_ratios.append((reuse / denom) if denom > 0 else 0.0)
            rescan_ratios.append((rescan / denom) if denom > 0 else 0.0)

            out_dir = tmp_root / f"out_{i}"
            _reset_dir(out_dir)
            decompress_corpus(result.archive, out_dir)
            if not _equal_dirs(input_dir, out_dir):
                lossless_ok = False

            # Determinism check in verification mode (fresh cache each time).
            det_cache = tmp_root / f"det_cache_{i}"
            _reset_dir(det_cache)
            r1 = compress_corpus_differential(input_dir, det_cache)
            _reset_dir(det_cache)
            r2 = compress_corpus_differential(input_dir, det_cache)
            if r1.archive != r2.archive:
                determinism_ok = False

        top_miss_reason = None
        if detailed_miss_reason_counts:
            top_miss_reason = max(
                sorted(detailed_miss_reason_counts.keys()),
                key=lambda k: detailed_miss_reason_counts[k],
            )
        estimated_benefit_partial_reuse = (
            mean(reusable_but_not_hit_values) if reusable_but_not_hit_values else 0.0
        )

        return {
            "workload_class": workload_class,
            "scenario": scenario,
            "run_count": run_count,
            "cache_hit_candidate_rate": hits / run_count if run_count else 0.0,
            "archives_equal_rate": equals / run_count if run_count else 0.0,
            "archives_equal_given_cache_hit_rate": (
                (equals / hits) if hits > 0 else None
            ),
            "reuse_chunk_ratio_avg": mean(reuse_ratios) if reuse_ratios else 0.0,
            "rescan_chunk_ratio_avg": mean(rescan_ratios) if rescan_ratios else 0.0,
            "reuse_chunk_distribution": {
                "min": min(reuse_chunk_values) if reuse_chunk_values else 0,
                "max": max(reuse_chunk_values) if reuse_chunk_values else 0,
                "avg": mean(reuse_chunk_values) if reuse_chunk_values else 0.0,
                "stdev": (
                    pstdev(reuse_chunk_values) if len(reuse_chunk_values) > 1 else 0.0
                ),
            },
            "rescan_chunk_distribution": {
                "min": min(rescan_chunk_values) if rescan_chunk_values else 0,
                "max": max(rescan_chunk_values) if rescan_chunk_values else 0,
                "avg": mean(rescan_chunk_values) if rescan_chunk_values else 0.0,
                "stdev": (
                    pstdev(rescan_chunk_values) if len(rescan_chunk_values) > 1 else 0.0
                ),
            },
            "cache_miss_reason_counts": dict(sorted(miss_reason_counts.items())),
            "detailed_miss_reason_counts": dict(
                sorted(detailed_miss_reason_counts.items())
            ),
            "top_miss_reason": top_miss_reason,
            "reusable_but_not_hit_chunks_avg": estimated_benefit_partial_reuse,
            "partial_reuse_opportunity_count": partial_reuse_opportunity_count,
            "estimated_benefit_if_partial_reuse_existed": estimated_benefit_partial_reuse,
            "total_time_ms_avg": mean(total_times_ms) if total_times_ms else 0.0,
            "total_time_ms_stdev": (
                pstdev(total_times_ms) if len(total_times_ms) > 1 else 0.0
            ),
            "lossless_status": "pass" if lossless_ok else "fail",
            "determinism_status": "pass" if determinism_ok else "fail",
            "sample_size": run_count,
        }


def run_harness(output_dir: Path | None = None, run_count: int = 20) -> Dict[str, Any]:
    if output_dir is None:
        output_dir = RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    cache_return_flag = False
    rows: List[Dict[str, Any]] = []
    for w_name, w_updater in WORKLOADS.items():
        for scenario_name in SCENARIOS.keys():
            if scenario_name == "unchanged":
                rows.append(
                    _run_workload(w_name, scenario_name, _scenario_unchanged, run_count)
                )
            elif scenario_name == "append-only" and w_name == "append-only logs":
                rows.append(_run_workload(w_name, scenario_name, w_updater, run_count))
            elif scenario_name == "small-change" and w_name in (
                "structured corpora",
                "mixed binaries",
            ):
                rows.append(_run_workload(w_name, scenario_name, w_updater, run_count))
            elif scenario_name == "noisy-change" and w_name == "noisy datasets":
                rows.append(_run_workload(w_name, scenario_name, w_updater, run_count))

    mutating_rows = [
        r
        for r in rows
        if r.get("scenario") in ("append-only", "small-change", "noisy-change")
    ]
    mutating_hit_avg = (
        mean([r["cache_hit_candidate_rate"] for r in mutating_rows])
        if mutating_rows
        else 0.0
    )
    mutating_eq_given_hit_values = [
        r["archives_equal_given_cache_hit_rate"]
        for r in mutating_rows
        if r["archives_equal_given_cache_hit_rate"] is not None
    ]
    mutating_eq_given_hit_avg = (
        mean(mutating_eq_given_hit_values) if mutating_eq_given_hit_values else 0.0
    )
    all_safe = all(
        r["lossless_status"] == "pass" and r["determinism_status"] == "pass"
        for r in rows
    )
    # Conservative gate: require strong stability on mutating scenarios.
    phase3_go = bool(
        all_safe
        and mutating_hit_avg >= 0.8
        and mutating_eq_given_hit_avg >= 0.99
        and all(
            (r["archives_equal_given_cache_hit_rate"] in (None, 1.0))
            for r in mutating_rows
        )
    )
    recommendation = (
        "go_phase3_candidate" if phase3_go else "no_go_keep_verification_mode"
    )

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verification_mode_only": True,
        "cache_return_enabled": cache_return_flag,
        "run_count_per_workload": run_count,
        "mutating_hit_rate_avg": mutating_hit_avg,
        "mutating_archives_equal_given_hit_avg": mutating_eq_given_hit_avg,
        "workloads": rows,
        "phase3_recommendation": recommendation,
    }

    (output_dir / JSON_PATH.name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Differential Hit-Rate Report",
        "",
        "- Verification mode only: `true`",
        f"- Cache return enabled: `{cache_return_flag}`",
        f"- Run count per workload: `{run_count}`",
        f"- Mutating hit-rate avg: `{mutating_hit_avg:.3f}`",
        f"- Mutating archives_equal|hit avg: `{mutating_eq_given_hit_avg:.3f}`",
        f"- Phase 3 recommendation: `{recommendation}`",
        "",
        "## Workloads",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"### {row['workload_class']} ({row['scenario']})",
                f"- run_count: {row['run_count']}",
                f"- cache_hit_candidate_rate: {row['cache_hit_candidate_rate']:.3f}",
                f"- archives_equal_rate: {row['archives_equal_rate']:.3f}",
                f"- archives_equal_given_cache_hit_rate: {row['archives_equal_given_cache_hit_rate']}",
                f"- top_miss_reason: {row['top_miss_reason']}",
                f"- partial_reuse_opportunity_count: {row['partial_reuse_opportunity_count']}",
                f"- reusable_but_not_hit_chunks_avg: {row['reusable_but_not_hit_chunks_avg']:.3f}",
                f"- estimated_benefit_if_partial_reuse_existed: {row['estimated_benefit_if_partial_reuse_existed']:.3f}",
                f"- reuse_chunk_ratio_avg: {row['reuse_chunk_ratio_avg']:.3f}",
                f"- rescan_chunk_ratio_avg: {row['rescan_chunk_ratio_avg']:.3f}",
                f"- total_time_ms_avg: {row['total_time_ms_avg']:.2f}",
                f"- total_time_ms_stdev: {row['total_time_ms_stdev']:.2f}",
                f"- lossless_status: {row['lossless_status']}",
                f"- determinism_status: {row['determinism_status']}",
                "",
            ]
        )
    (output_dir / MARKDOWN_PATH.name).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run differential verification-mode hit-rate harness."
    )
    parser.add_argument(
        "--output-dir",
        default=str(RESULTS_DIR),
        help="Output directory for JSON/markdown report.",
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
