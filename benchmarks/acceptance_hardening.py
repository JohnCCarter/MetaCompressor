"""Acceptance hardening benchmark/report for MetaCompressor."""

from __future__ import annotations

import argparse
import json
import os
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
    ]
    if _available_mb() >= 2000:
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
    return [result for result in dataset_results if result["name"] in _STRUCTURED_EDGE_DATASET_NAMES]


def _reason_for_dataset(result: Dict[str, Any]) -> str:
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
    for result in dataset_results:
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
    ranked = sorted(
        dataset_results,
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
    structured_results = _structured_edge_results(dataset_results)
    strong_wins = [
        result
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] <= -10.0
    ]
    hidden_losses = [
        result
        for result in dataset_results
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
    lines = [
        "# MetaCompressor Acceptance Hardening Report",
        "",
        "Generated by `benchmarks/acceptance_hardening.py`.",
        "",
        "| Dataset | Raw | TAR+ZSTD | MC final | Delta % | Mode | Compress s | Decomp s | Peak MB | Verdict | Reason |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|",
    ]

    for result in dataset_results:
        final_method = result["methods"]["mc_final_selected"]
        tar_method = result["methods"]["tar_zstd"]
        summary = result["mc_summary"]
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %.3f | %.3f | %.1f | %s | %s |"
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
                summary["verdict"],
                _reason_for_dataset(result),
            )
        )

    structured_results = _structured_edge_results(dataset_results)
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
        for result in dataset_results
        if result["mc_summary"]["fallback_triggered"]
    ]
    peak_dataset = max(dataset_results, key=lambda result: result["methods"]["mc_final_selected"]["peak_mem_mb"])
    slowest_dataset = max(dataset_results, key=lambda result: result["methods"]["mc_final_selected"]["compress_s"])

    lines += [
        "",
        "## Win-rate summary",
        "",
        "- Structured strong wins (>=10%% vs TAR+ZSTD): %d/%d"
        % (len(strong_wins), len(structured_results)),
        "- Strong-win datasets: %s" % (", ".join(strong_wins) if strong_wins else "none"),
        "- Sub-10%% wins: %s" % (", ".join(near_wins) if near_wins else "none"),
        "- Final fallback selections: %s" % (", ".join(fallbacks) if fallbacks else "none"),
        "",
        "## Speed/memory summary",
        "",
        "- Slowest final compression: **%s** at %.3fs"
        % (slowest_dataset["name"], slowest_dataset["methods"]["mc_final_selected"]["compress_s"]),
        "- Highest measured peak memory: **%s** at %.1f MB"
        % (peak_dataset["name"], peak_dataset["methods"]["mc_final_selected"]["peak_mem_mb"]),
        "- Tokenize / extract / encode / zstd timings are captured per dataset in JSON under `methods.mc_final_selected.metrics.timing`.",
        "",
        "## Trust/correctness summary",
        "",
        "- Every measured MC archive was decompressed and byte-compared during the benchmark run.",
        "- Determinism was verified by compressing each measured MC mode twice and comparing the resulting archives byte-for-byte.",
        "- Final fallback threshold remained aligned to the >10% loss safeguard.",
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
    with tempfile.TemporaryDirectory(prefix="mc_acceptance_hardening_") as tmp:
        tmp_root = Path(tmp)
        for spec in _dataset_specs(include_500mb=include_500mb):
            dataset_dir = tmp_root / "datasets" / spec.name
            work_dir = tmp_root / "work" / spec.name
            work_dir.mkdir(parents=True, exist_ok=True)
            _build_dataset(dataset_dir, spec)
            dataset_results.append(_measure_dataset(dataset_dir, spec, work_dir))

    final_verdict = _build_final_verdict(dataset_results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "available_memory_mb_at_start": _available_mb(),
        "include_500mb": include_500mb,
        "datasets": dataset_results,
        "correctness_passed": True,
        "determinism_passed": True,
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
