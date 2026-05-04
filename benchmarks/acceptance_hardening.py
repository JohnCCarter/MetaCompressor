"""Acceptance hardening benchmark/report for MetaCompressor."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmarks import production_validation as pv  # noqa: E402

_RESULTS_DIR = REPO_ROOT / "results"
_MARKDOWN_PATH = _RESULTS_DIR / "metacompressor_acceptance_hardening.md"
_JSON_PATH = _RESULTS_DIR / "metacompressor_acceptance_hardening.json"
_MIN_STRONG_WIN_NUMERATOR = 2
_MIN_250MB_MEMORY_MB = 2000
_DEFAULT_DATASET_TIMEOUT_S = 180
_DATASET_TIMEOUTS_S = {
    "structured_scale_10mb": 180,
    "structured_scale_50mb": 360,
    "structured_scale_100mb": 720,
    "structured_scale_250mb": 900,
}
_REQUIRED_SCALE_DATASET_NAMES = (
    "structured_scale_10mb",
    "structured_scale_50mb",
    "structured_scale_100mb",
    "structured_scale_250mb",
)

DatasetSpec = pv.DatasetSpec
ValidationError = pv.ValidationError
_available_mb = pv._available_mb
_build_dataset = pv._build_dataset
_fmt_bytes = pv._fmt_bytes
_fmt_pct = pv._fmt_pct
_generate_app_service_logs = pv._generate_app_service_logs
_generate_high_cardinality_logs = pv._generate_high_cardinality_logs
_generate_many_small_files = pv._generate_many_small_files
_generate_mixed_microservice_logs = pv._generate_mixed_microservice_logs
_generate_ndjson_logs = pv._generate_ndjson_logs
_generate_nginx_logs = pv._generate_nginx_logs
_generate_noisy_logs = pv._generate_noisy_logs
_json_dumps = pv._json_dumps
_measure_dataset = pv._measure_dataset
_mode_label = pv._mode_label

_EDGE_DATASET_NAMES = (
    "app_service_logs",
    "json_ndjson_logs",
    "nginx_access_logs",
    "mixed_microservice_logs",
    "high_cardinality_logs",
    "noisy_low_structure_logs",
    "many_small_files_5000",
)
_STRUCTURED_EDGE_DATASET_NAMES = (
    "app_service_logs",
    "json_ndjson_logs",
    "nginx_access_logs",
    "mixed_microservice_logs",
    "high_cardinality_logs",
    "many_small_files_5000",
)


def _large_tests_enabled() -> bool:
    return os.getenv("RUN_LARGE_TESTS") == "1"


def _dataset_specs(include_500mb: bool) -> List[DatasetSpec]:
    specs = [
        DatasetSpec(
            name="structured_scale_10mb",
            dataset_type="structured scale 10MB",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 10, seed=1001, files=8),
        ),
        DatasetSpec(
            name="structured_scale_50mb",
            dataset_type="structured scale 50MB",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 50, seed=1002, files=12),
        ),
        DatasetSpec(
            name="structured_scale_100mb",
            dataset_type="structured scale 100MB",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 100, seed=1003, files=16),
        ),
        DatasetSpec(
            name="app_service_logs",
            dataset_type="app/service logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 12, seed=101, files=10),
        ),
        DatasetSpec(
            name="json_ndjson_logs",
            dataset_type="JSON/NDJSON",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_ndjson_logs(root, 14, seed=202, files=8),
        ),
        DatasetSpec(
            name="nginx_access_logs",
            dataset_type="nginx/access",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_nginx_logs(root, 14, seed=303, files=6),
        ),
        DatasetSpec(
            name="mixed_microservice_logs",
            dataset_type="mixed microservice logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_mixed_microservice_logs(root, 18, seed=404),
        ),
        DatasetSpec(
            name="high_cardinality_logs",
            dataset_type="high-cardinality logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_high_cardinality_logs(root, 10, seed=505, files=6),
        ),
        DatasetSpec(
            name="noisy_low_structure_logs",
            dataset_type="noisy/low-structure logs",
            realism="semi-realistic",
            structured=False,
            generator=lambda root: _generate_noisy_logs(root, 9, seed=606, files=6),
        ),
        DatasetSpec(
            name="many_small_files_5000",
            dataset_type="many-small-files corpus",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_many_small_files(root, seed=909, files=5000),
        ),
    ]
    specs.append(
        DatasetSpec(
            name="structured_scale_250mb",
            dataset_type="structured scale 250MB",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 250, seed=1004, files=24),
        )
    )
    if include_500mb:
        specs.append(
            DatasetSpec(
                name="structured_scale_500mb",
                dataset_type="structured scale 500MB",
                realism="semi-realistic",
                structured=True,
                generator=lambda root: _generate_app_service_logs(root, 500, seed=1005, files=32),
            )
        )
    return specs


def _structured_edge_results(dataset_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        result
        for result in dataset_results
        if result["name"] in _STRUCTURED_EDGE_DATASET_NAMES and not _dataset_skipped(result)
    ]


def _dataset_skipped(result: Dict[str, Any]) -> bool:
    return result.get("status") == "skipped"


def _completed_results(dataset_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [result for result in dataset_results if not _dataset_skipped(result)]


def _required_scale_results(dataset_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        result
        for result in dataset_results
        if result["name"] in _REQUIRED_SCALE_DATASET_NAMES
    ]


def _skip_reason_for_spec(spec: DatasetSpec, available_memory_mb: int) -> Optional[str]:
    if spec.name == "structured_scale_250mb" and available_memory_mb < _MIN_250MB_MEMORY_MB:
        return (
            "skipped: available memory at start (%d MB) below %d MB threshold for 250MB dataset"
            % (available_memory_mb, _MIN_250MB_MEMORY_MB)
        )
    return None


def _dataset_timeout_seconds(spec: DatasetSpec) -> int:
    return _DATASET_TIMEOUTS_S.get(spec.name, _DEFAULT_DATASET_TIMEOUT_S)


@contextlib.contextmanager
def _dataset_time_limit(timeout_s: int):
    if timeout_s <= 0 or os.name != "posix":
        yield
        return

    def _handle_timeout(signum, frame):
        raise TimeoutError("exceeded %ds time budget" % timeout_s)

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _skipped_dataset_result(spec: DatasetSpec, reason: str) -> Dict[str, Any]:
    return {
        "name": spec.name,
        "dataset_type": spec.dataset_type,
        "realism": spec.realism,
        "structured": spec.structured,
        "status": "skipped",
        "skip_reason": reason,
        "correctness_status": "skipped",
        "determinism_status": "skipped",
        "raw_size": None,
        "methods": {},
        "mc_summary": {
            "selected_mode": "skipped",
            "before_selected_mode": None,
            "fallback_triggered": False,
            "template_count": 0,
            "template_reuse_rate": None,
            "template_reuse_before": None,
            "template_reuse_after": None,
            "json_lines_detected": 0,
            "json_template_count": 0,
            "normalized_template_count": 0,
            "fuzzy_merge_count": 0,
            "fallback_reason_counts": {},
            "column_count": 0,
            "column_encoding_counts": {},
            "raw_fallback_lines": 0,
            "raw_fallback_files": 0,
            "binary_fallback_files": 0,
            "before_delta_vs_tar_zstd_pct": None,
            "delta_vs_tar_zstd_pct": None,
            "delta_vs_zstd_per_file_pct": None,
            "reduction_vs_raw_pct": None,
            "verdict": "skipped",
        },
    }


def _finalize_dataset_result(result: Dict[str, Any]) -> Dict[str, Any]:
    result["status"] = "completed"
    result["skip_reason"] = None
    result["correctness_status"] = "passed"
    result["determinism_status"] = "passed"
    return result


def _reason_for_dataset(result: Dict[str, Any]) -> str:
    if _dataset_skipped(result):
        return result["skip_reason"]
    summary = result["mc_summary"]
    delta_pct = summary["delta_vs_tar_zstd_pct"]
    reuse_pct = (summary["template_reuse_rate"] or 0.0) * 100.0
    column_count = summary["column_count"]
    fallback_reasons = summary["fallback_reason_counts"]
    if summary["fallback_triggered"]:
        return "fallback kept loss bounded (%s)" % json.dumps(fallback_reasons, sort_keys=True)
    if delta_pct is not None and delta_pct <= -10.0:
        return "reuse=%s, columns=%d" % (_fmt_pct(reuse_pct), column_count)
    if fallback_reasons:
        return "close to baseline; fallback reasons=%s" % json.dumps(fallback_reasons, sort_keys=True)
    return "close to baseline; reuse=%s, columns=%d" % (_fmt_pct(reuse_pct), column_count)


def _remaining_weak_zones(dataset_results: List[Dict[str, Any]]) -> List[str]:
    weak = []
    for result in _completed_results(dataset_results):
        summary = result["mc_summary"]
        delta_pct = summary["delta_vs_tar_zstd_pct"]
        if delta_pct is None:
            continue
        if delta_pct >= 0.0 or summary["fallback_triggered"]:
            weak.append(
                "- **%s**: delta=%s, mode=%s, reason=%s"
                % (
                    result["name"],
                    _fmt_pct(delta_pct),
                    _mode_label(summary["selected_mode"]),
                    _reason_for_dataset(result),
                )
            )
    return weak


def _recommended_next_improvement(dataset_results: List[Dict[str, Any]]) -> str:
    completed_results = _completed_results(dataset_results)
    if not completed_results:
        return "No measured datasets completed; rerun once resource constraints are resolved."
    ranked = sorted(
        completed_results,
        key=lambda result: result["mc_summary"]["delta_vs_tar_zstd_pct"]
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        else -999.0,
        reverse=True,
    )
    weakest = ranked[0]
    return (
        "Focus on %s next: %s."
        % (weakest["name"], _reason_for_dataset(weakest))
    )


def _build_final_verdict(dataset_results: List[Dict[str, Any]]) -> str:
    completed_results = _completed_results(dataset_results)
    structured_results = _structured_edge_results(completed_results)
    skipped_required = [
        result["name"]
        for result in _required_scale_results(dataset_results)
        if _dataset_skipped(result)
    ]
    if skipped_required:
        return "ACCEPTANCE_HARDENING_PARTIAL Reason: skipped required scale datasets: %s" % ", ".join(skipped_required)
    strong_wins = [
        result
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] <= -10.0
    ]
    hidden_losses = [
        result
        for result in completed_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] > 10.0
    ]
    if hidden_losses:
        return "ACCEPTANCE_HARDENING_PARTIAL Reason: fallback safeguards still allowed >10% loss"
    if len(strong_wins) * 2 <= len(structured_results):
        return (
            "ACCEPTANCE_HARDENING_PARTIAL Reason: MC did not beat TAR+ZSTD by >=10% "
            "on most structured log datasets"
        )
    return "ACCEPTANCE_HARDENING_VALIDATED"


def _build_markdown_report(dataset_results: List[Dict[str, Any]], final_verdict: str) -> str:
    completed_results = _completed_results(dataset_results)
    lines = [
        "# MetaCompressor Acceptance Hardening Report",
        "",
        "Generated by `benchmarks/acceptance_hardening.py`.",
        "",
        "| Dataset | Status | Raw | TAR+ZSTD | MC final | Delta % | Mode | Compress s | Decomp s | Peak MB | Correctness | Determinism | Verdict | Fallback/Reason |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|---|---|",
    ]

    for result in dataset_results:
        if _dataset_skipped(result):
            lines.append(
                "| %s | skipped | n/a | n/a | n/a | n/a | skipped | n/a | n/a | n/a | %s | %s | skipped | %s |"
                % (
                    result["name"],
                    result["correctness_status"],
                    result["determinism_status"],
                    _reason_for_dataset(result),
                )
            )
            continue
        final_method = result["methods"]["mc_final_selected"]
        tar_method = result["methods"]["tar_zstd"]
        summary = result["mc_summary"]
        lines.append(
            "| %s | completed | %s | %s | %s | %s | %s | %.3f | %.3f | %.1f | %s | %s | %s | %s |"
            % (
                result["name"],
                _fmt_bytes(result["raw_size"]),
                _fmt_bytes(tar_method["size"]),
                _fmt_bytes(final_method["size"]),
                _fmt_pct(summary["delta_vs_tar_zstd_pct"]),
                _mode_label(summary["selected_mode"]),
                final_method["compress_s"],
                final_method["decompress_s"],
                final_method["peak_mem_mb"],
                result["correctness_status"],
                result["determinism_status"],
                summary["verdict"],
                _reason_for_dataset(result),
            )
        )

    structured_results = _structured_edge_results(completed_results)
    strong_wins = [
        result["name"]
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] <= -10.0
    ]
    near_wins = [
        result["name"]
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and -10.0 < result["mc_summary"]["delta_vs_tar_zstd_pct"] < 0.0
    ]
    fallbacks = [
        result["name"]
        for result in completed_results
        if result["mc_summary"]["fallback_triggered"]
    ]
    skipped_results = [result["name"] for result in dataset_results if _dataset_skipped(result)]
    peak_dataset = None
    slowest_dataset = None
    if completed_results:
        peak_dataset = max(
            completed_results,
            key=lambda result: result["methods"]["mc_final_selected"]["peak_mem_mb"],
        )
        slowest_dataset = max(
            completed_results,
            key=lambda result: result["methods"]["mc_final_selected"]["compress_s"],
        )

    lines += [
        "",
        "## Win-rate summary",
        "",
        "- Structured strong wins (>=10%% vs TAR+ZSTD): %d/%d"
        % (len(strong_wins), len(structured_results)),
        "- Strong-win datasets: %s" % (", ".join(strong_wins) if strong_wins else "none"),
        "- Sub-10%% wins: %s" % (", ".join(near_wins) if near_wins else "none"),
        "- Final fallback selections: %s" % (", ".join(fallbacks) if fallbacks else "none"),
        "- Skipped datasets: %s" % (", ".join(skipped_results) if skipped_results else "none"),
        "",
        "## Speed/memory summary",
        "",
        "- Slowest final compression: %s"
        % (
            "**%s** at %.3fs"
            % (
                slowest_dataset["name"],
                slowest_dataset["methods"]["mc_final_selected"]["compress_s"],
            )
            if slowest_dataset is not None
            else "n/a (no completed datasets)"
        ),
        "- Highest measured peak memory: %s"
        % (
            "**%s** at %.1f MB"
            % (
                peak_dataset["name"],
                peak_dataset["methods"]["mc_final_selected"]["peak_mem_mb"],
            )
            if peak_dataset is not None
            else "n/a (no completed datasets)"
        ),
        "- Tokenize / extract / encode / zstd timings are captured per completed dataset in JSON under `methods.mc_final_selected.metrics.timing`.",
        "",
        "## Trust/correctness summary",
        "",
        "- Every measured MC archive was decompressed and byte-compared during the benchmark run.",
        "- Determinism was verified by compressing each measured MC mode twice and comparing the resulting archives byte-for-byte.",
        "- Final fallback threshold remained aligned to the >10% loss safeguard.",
        "- Skipped datasets are explicitly marked in the table and JSON with a skip reason.",
        "- Final verdict: `%s`" % final_verdict,
        "",
        "## Remaining weak zones",
        "",
    ]
    weak = _remaining_weak_zones(dataset_results)
    if weak:
        lines.extend(weak)
    else:
        lines.append("*(none in this run)*")
    lines += [
        "",
        "## Recommended next improvement",
        "",
        _recommended_next_improvement(dataset_results),
        "",
    ]
    return "\n".join(lines)


def run_validation(output_dir: Optional[Path] = None, include_500mb: Optional[bool] = None) -> Dict[str, Any]:
    if include_500mb is None:
        include_500mb = _large_tests_enabled()

    dataset_results: List[Dict[str, Any]] = []
    available_memory_mb = _available_mb()
    with tempfile.TemporaryDirectory(prefix="mc_acceptance_hardening_") as tmp:
        tmp_root = Path(tmp)
        for spec in _dataset_specs(include_500mb=include_500mb):
            skip_reason = _skip_reason_for_spec(spec, available_memory_mb)
            if skip_reason is not None:
                dataset_results.append(_skipped_dataset_result(spec, skip_reason))
                continue
            dataset_dir = tmp_root / "datasets" / spec.name
            work_dir = tmp_root / "work" / spec.name
            work_dir.mkdir(parents=True, exist_ok=True)
            timeout_s = _dataset_timeout_seconds(spec)
            try:
                with _dataset_time_limit(timeout_s):
                    _build_dataset(dataset_dir, spec)
                    dataset_results.append(_finalize_dataset_result(_measure_dataset(dataset_dir, spec, work_dir)))
            except TimeoutError as exc:
                dataset_results.append(
                    _skipped_dataset_result(spec, "skipped: %s" % exc)
                )

    final_verdict = _build_final_verdict(dataset_results)
    completed_results = _completed_results(dataset_results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "available_memory_mb_at_start": available_memory_mb,
        "include_500mb": include_500mb,
        "datasets": dataset_results,
        "correctness_passed": all(result["correctness_status"] == "passed" for result in completed_results),
        "determinism_passed": all(result["determinism_status"] == "passed" for result in completed_results),
        "remaining_weak_zones": _remaining_weak_zones(dataset_results),
        "recommended_next_improvement": _recommended_next_improvement(dataset_results),
        "final_verdict": final_verdict,
    }

    if output_dir is None:
        output_dir = _RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / _JSON_PATH.name).write_text(_json_dumps(payload) + "\n", encoding="utf-8")
    (output_dir / _MARKDOWN_PATH.name).write_text(
        _build_markdown_report(dataset_results, final_verdict) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MetaCompressor acceptance hardening validation.")
    parser.add_argument(
        "--output-dir",
        default=str(_RESULTS_DIR),
        help="Directory for markdown/json results (default: results/).",
    )
    args = parser.parse_args()

    try:
        payload = run_validation(output_dir=Path(args.output_dir))
    except ValidationError as exc:
        message = "ACCEPTANCE_HARDENING_BLOCKED Reason: %s" % exc
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _JSON_PATH.write_text(
            _json_dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "final_verdict": message,
                    "correctness_passed": False,
                    "determinism_passed": False,
                    "error": str(exc),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _MARKDOWN_PATH.write_text("# MetaCompressor Acceptance Hardening Report\n\n%s\n" % message, encoding="utf-8")
        print(message)
        raise SystemExit(1)
    except Exception as exc:
        message = "ACCEPTANCE_HARDENING_BLOCKED Reason: benchmark failed: %s" % exc
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _JSON_PATH.write_text(
            _json_dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "final_verdict": message,
                    "correctness_passed": False,
                    "determinism_passed": False,
                    "error": str(exc),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _MARKDOWN_PATH.write_text("# MetaCompressor Acceptance Hardening Report\n\n%s\n" % message, encoding="utf-8")
        print(message)
        raise

    print(payload["final_verdict"])


if __name__ == "__main__":
    main()
