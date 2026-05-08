"""Tests for the acceptance hardening benchmark script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from metacompressor.tests.path_utils import repository_root

_MODULE_PATH = (
    repository_root(Path(__file__))
    / "benchmarks"
    / "acceptance"
    / "acceptance_hardening.py"
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


def _run_dataset_with_timeout_inprocess(
    tmp_root: Path,
    spec,
    timeout_s: int,
    benchmark_mode: str = "full",
    differential_report_enabled: bool = False,
):
    """Run dataset measurement in-process (benchmark module is importlib-loaded; Windows spawn cannot re-import it)."""
    dataset_dir = tmp_root / "datasets" / spec.name
    work_dir = tmp_root / "work" / spec.name
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        acceptance_hardening._build_dataset(dataset_dir, spec)
        if benchmark_mode == "quick":
            measured = acceptance_hardening._measure_dataset_quick(
                dataset_dir,
                spec,
                work_dir,
                differential_report_enabled=differential_report_enabled,
            )
        else:
            measured = acceptance_hardening._measure_dataset(
                dataset_dir, spec, work_dir
            )
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
    assert "## Speed/memory summary" in markdown
    assert "## Trust/correctness summary" in markdown
    assert "## Remaining weak zones" in markdown
    assert "## Recommended next improvement" in markdown


def test_quick_decision_report_includes_scores(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)

    result = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        acceptance_hardening.DatasetSpec(
            name="tiny_fixture",
            dataset_type="app/service logs",
            realism="semi-realistic",
            structured=True,
            generator=_generate_small_corpus_for_validation,
        ),
        work_dir,
    )
    report = result["decision_report"]
    features = report["decision_features"]

    assert report["benchmark_mode"] == "quick"
    assert "selected_path" in report
    assert "baseline_path" in report
    assert "decision_reason" in report
    assert "confidence_score" in features
    assert "columnar_score" in features
    assert "row_reuse_score" in features
    assert "estimated_entropy_sample" in features
    assert "zstd_affinity_score" in report
    assert "shaping_candidates" in report
    assert "expected_zstd_benefit_reason" in report
    assert "unsafe_to_shape_reason" in report
    assert "receipt_load_time_ms" in report
    assert "bounded_scan_time_ms" in report
    assert "receipt_validation_time_ms" in report
    assert "manifest_validation_time_ms" in report
    assert "decision_kernel_total_time_ms" in report
    assert "receipt_reuse_saved_scan_estimate_ms" in report
    assert "analysis_skip_eligible" in report
    assert "analysis_skip_used" in report
    assert "analysis_skip_denied_reason" in report
    assert "warm_path_used" in report
    assert "warm_path_saved_estimate_ms" in report
    assert "receipt_used" in report
    assert "receipt_valid" in report
    assert "baseline_tar_zstd_time_ms" in report
    assert "mc_selected_build_time_ms" in report
    assert "mc_selected_serialize_time_ms" in report
    assert "mc_selected_zstd_time_ms" in report
    assert "correctness_verify_time_ms" in report
    assert "determinism_verify_time_ms" in report
    assert "sidecar_write_time_ms" in report
    assert "total_quick_time_ms" in report
    assert "input_walk_time_ms" in report
    assert "template_normalization_time_ms" in report
    assert "row_grouping_time_ms" in report
    assert "columnar_detection_time_ms" in report
    assert "chunk_dedupe_time_ms" in report
    assert "delta_reuse_time_ms" in report
    assert "msgpack_object_build_time_ms" in report
    assert "memory_copy_materialization_time_ms" in report
    assert "files_processed" in report
    assert "chunks_processed" in report
    assert "rows_processed_estimate" in report
    assert "templates_detected" in report
    assert "dedupe_hits" in report
    assert "intermediate_bytes_built" in report
    assert "runner_setup_time_ms" in report
    assert "config_load_time_ms" in report
    assert "input_copy_or_staging_time_ms" in report
    assert "compressor_init_time_ms" in report
    assert "selected_mode_dispatch_time_ms" in report
    assert "selected_mode_resolve_time_ms" in report
    assert "input_model_prepare_time_ms" in report
    assert "transform_call_time_ms" in report
    assert "output_model_finalize_time_ms" in report
    assert "metrics_finalize_time_ms" in report
    assert "dispatch_explained_time_ms" in report
    assert "dispatch_unexplained_time_ms" in report
    assert "dispatch_explained_pct" in report
    assert "source_read_time_ms" in report
    assert "file_record_build_time_ms" in report
    assert "template_extract_time_ms" in report
    assert "normalization_apply_time_ms" in report
    assert "row_model_build_time_ms" in report
    assert "dedupe_index_build_time_ms" in report
    assert "payload_assembly_time_ms" in report
    assert "final_pack_time_ms" in report
    assert "final_zstd_time_ms" in report
    assert "transform_explained_time_ms" in report
    assert "transform_unexplained_time_ms" in report
    assert "transform_explained_pct" in report
    assert "line_split_time_ms" in report
    assert "tokenization_time_ms" in report
    assert "pattern_match_time_ms" in report
    assert "placeholder_detection_time_ms" in report
    assert "template_hash_time_ms" in report
    assert "template_grouping_time_ms" in report
    assert "template_cache_lookup_time_ms" in report
    assert "lines_scanned" in report
    assert "tokens_scanned" in report
    assert "regex_match_count" in report
    assert "templates_created" in report
    assert "template_cache_hits" in report
    assert "template_cache_misses" in report
    assert "template_extract_call_count" in report
    assert "tokenize_one_file_call_count" in report
    assert "regex_compile_time_ms" in report
    assert "regex_apply_time_ms" in report
    assert "shared_memory_pack_time_ms" in report
    assert "shared_memory_unpack_time_ms" in report
    assert "worker_startup_time_ms" in report
    assert "per_call_overhead_time_ms" in report
    assert "tokenization_pattern_double_count" in report
    assert "template_extract_exclusive_sum_ms" in report
    assert "timing_anomaly" in report
    assert "template_extract_wall_time_ms" in report
    assert "template_extract_child_work_time_ms" in report
    assert "template_extract_parent_wait_time_ms" in report
    assert "template_extract_queue_submit_time_ms" in report
    assert "template_extract_result_collect_time_ms" in report
    assert "template_extract_unexplained_time_ms" in report
    assert "inline_template_extract_used" in report
    assert "inline_template_extract_reason" in report
    assert "inline_template_extract_time_ms" in report
    assert "template_extract_saved_estimate_ms" in report
    assert report["tokenization_pattern_double_count"] is False
    assert "output_collect_time_ms" in report
    assert "metrics_collect_time_ms" in report
    assert "subprocess_or_import_overhead_ms" in report
    assert "explained_build_time_ms" in report
    assert "unexplained_build_time_ms" in report
    assert "explained_build_pct" in report
    assert "differential_report" not in report


def test_quick_decision_scores_deterministic(tmp_path):
    dataset_root = tmp_path / "dataset"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )

    r1 = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, tmp_path / "w1"
    )
    r2 = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, tmp_path / "w2"
    )
    assert (
        r1["decision_report"]["decision_features"]
        == r2["decision_report"]["decision_features"]
    )
    assert (
        r1["decision_report"]["zstd_affinity_score"]
        == r2["decision_report"]["zstd_affinity_score"]
    )
    assert (
        r1["decision_report"]["shaping_candidates"]
        == r2["decision_report"]["shaping_candidates"]
    )
    assert (
        r1["decision_report"]["expected_zstd_benefit_reason"]
        == r2["decision_report"]["expected_zstd_benefit_reason"]
    )
    assert (
        r1["decision_report"]["unsafe_to_shape_reason"]
        == r2["decision_report"]["unsafe_to_shape_reason"]
    )


def test_quick_scorer_failure_does_not_change_compression_result(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )

    baseline = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, tmp_path / "w_ok"
    )
    baseline_size = baseline["methods"]["mc_final_selected"]["size"]

    monkeypatch.setattr(
        acceptance_hardening,
        "_decision_kernel_features",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    failed = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, tmp_path / "w_fail"
    )
    failed_size = failed["methods"]["mc_final_selected"]["size"]

    assert failed["decision_report"]["scorer_failed"] is True
    assert baseline_size == failed_size


def test_quick_mc_receipt_reused_when_fingerprint_matches(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )

    first = acceptance_hardening._measure_dataset_quick(dataset_root, spec, work_dir)
    assert first["decision_report"]["receipt_used"] is False

    monkeypatch.setattr(
        acceptance_hardening,
        "_decision_kernel_features",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("should not be called")
        ),
    )
    second = acceptance_hardening._measure_dataset_quick(dataset_root, spec, work_dir)
    assert second["decision_report"]["receipt_used"] is True
    assert second["decision_report"]["receipt_valid"] is True
    assert second["decision_report"]["scorer_failed"] is False
    assert second["decision_report"]["receipt_reuse_saved_scan_estimate_ms"] >= 0


def test_quick_mc_receipt_invalid_when_dataset_changes(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    acceptance_hardening._measure_dataset_quick(dataset_root, spec, work_dir)

    (dataset_root / "app.log").write_text(
        "changed content\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        acceptance_hardening,
        "_decision_kernel_features",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    changed = acceptance_hardening._measure_dataset_quick(dataset_root, spec, work_dir)
    assert changed["decision_report"]["receipt_used"] is False
    assert changed["decision_report"]["receipt_valid"] is False
    assert changed["decision_report"]["scorer_failed"] is True


def test_quick_differential_report_cold_run(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    result = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    report = result["decision_report"]["differential_report"]
    assert report["manifest_chunk_count"] > 0
    assert report["reuse_allowed"] is False
    assert report["reuse_reason"] == "missing_or_invalid_previous_manifest"
    assert "manifest_build_time_ms" in report
    assert "previous_manifest_load_time_ms" in report
    assert "diff_time_ms" in report
    assert "reuse_plan_time_ms" in report
    assert "differential_total_time_ms" in report
    assert "estimated_rescan_avoided_chunks" in report
    assert "estimated_reuse_ratio_pct" in report
    assert (work_dir / ".mcmanifest.json").exists()


def test_quick_differential_report_warm_run(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    second = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    report = second["decision_report"]["differential_report"]
    assert report["reusable_chunk_count"] == report["manifest_chunk_count"]
    assert report["rescan_chunk_count"] == 0
    assert report["reuse_allowed"] is True
    assert report["estimated_reuse_ratio_pct"] >= 99.0
    assert second["decision_report"]["analysis_skip_eligible"] is True
    assert second["decision_report"]["analysis_skip_used"] is True
    assert second["decision_report"]["warm_path_used"] is True


def test_quick_differential_report_changed_dataset(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    dataset_root.mkdir(parents=True, exist_ok=True)
    base = (b"A" * (1024 * 1024)) + (b"B" * (1024 * 1024))
    (dataset_root / "app.log").write_bytes(base)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    changed_bytes = bytearray(base)
    changed_bytes[-1] = ord("C")
    (dataset_root / "app.log").write_bytes(bytes(changed_bytes))
    changed = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    report = changed["decision_report"]["differential_report"]
    assert report["changed_chunk_count"] > 0
    assert report["rescan_chunk_count"] > 0
    assert report["reusable_chunk_count"] > 0
    assert report["estimated_rescan_avoided_chunks"] < report["manifest_chunk_count"]
    assert changed["decision_report"]["analysis_skip_eligible"] is False
    assert changed["decision_report"]["analysis_skip_used"] is False
    assert changed["decision_report"]["analysis_skip_denied_reason"] in (
        "changed_chunk_detected",
        "rescan_chunks_present",
        "stale_or_missing_receipt",
    )


def test_quick_differential_invalid_receipt_disables_skip(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    (work_dir / ".mcmeta").write_text("{}", encoding="utf-8")
    out = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    assert out["decision_report"]["analysis_skip_used"] is False
    assert (
        out["decision_report"]["analysis_skip_denied_reason"]
        == "stale_or_missing_receipt"
    )


def test_quick_differential_low_confidence_disables_skip(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    receipt_path = work_dir / ".mcmeta"
    payload = acceptance_hardening.json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["confidence_score"] = 0.01
    receipt_path.write_text(
        acceptance_hardening.json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    out = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    assert out["decision_report"]["analysis_skip_used"] is False
    assert out["decision_report"]["analysis_skip_denied_reason"] == "low_confidence"


def test_quick_differential_receipt_metadata_mismatch_disables_skip(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    receipt_path = work_dir / ".mcmeta"
    payload = acceptance_hardening.json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["selected_path_hint"] = ""
    receipt_path.write_text(
        acceptance_hardening.json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    out = acceptance_hardening._measure_dataset_quick(
        dataset_root,
        spec,
        work_dir,
        differential_report_enabled=True,
    )
    assert out["decision_report"]["analysis_skip_used"] is False
    assert (
        out["decision_report"]["analysis_skip_denied_reason"]
        == "receipt_metadata_mismatch"
    )


def test_analysis_skip_does_not_change_output_size_or_mode(tmp_path):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    cold = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, work_dir, differential_report_enabled=True
    )
    warm = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, work_dir, differential_report_enabled=True
    )
    assert warm["decision_report"]["analysis_skip_used"] is True
    assert (
        cold["methods"]["mc_final_selected"]["size"]
        == warm["methods"]["mc_final_selected"]["size"]
    )
    assert cold["mc_summary"]["selected_mode"] == warm["mc_summary"]["selected_mode"]


def test_inline_template_extract_small_corpus_used(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    monkeypatch.setenv("MC_MAX_FILES_INLINE_TEMPLATE_EXTRACT", "8")
    monkeypatch.setenv("MC_MAX_BYTES_INLINE_TEMPLATE_EXTRACT", "1048576")
    monkeypatch.setenv("MC_MAX_LINES_INLINE_TEMPLATE_EXTRACT", "5000")
    out = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, work_dir, differential_report_enabled=True
    )
    assert out["decision_report"]["inline_template_extract_used"] is True


def test_inline_template_extract_large_corpus_not_used(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    work_dir = tmp_path / "work"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "big.bin").write_bytes(b"A" * (2 * 1024 * 1024))
    spec = acceptance_hardening.DatasetSpec(
        name="big_fixture",
        dataset_type="bytes",
        realism="synthetic",
        structured=False,
        generator=lambda _p: None,
    )
    monkeypatch.setenv("MC_MAX_FILES_INLINE_TEMPLATE_EXTRACT", "8")
    monkeypatch.setenv("MC_MAX_BYTES_INLINE_TEMPLATE_EXTRACT", "262144")
    monkeypatch.setenv("MC_MAX_LINES_INLINE_TEMPLATE_EXTRACT", "5000")
    out = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, work_dir, differential_report_enabled=True
    )
    assert out["decision_report"]["inline_template_extract_used"] is False


def test_inline_template_extract_output_identical_to_existing_path(
    tmp_path, monkeypatch
):
    dataset_root = tmp_path / "dataset"
    work_dir_a = tmp_path / "work_a"
    work_dir_b = tmp_path / "work_b"
    _generate_small_corpus_for_validation(dataset_root)
    spec = acceptance_hardening.DatasetSpec(
        name="tiny_fixture",
        dataset_type="app/service logs",
        realism="semi-realistic",
        structured=True,
        generator=_generate_small_corpus_for_validation,
    )
    monkeypatch.setenv("MC_MAX_FILES_INLINE_TEMPLATE_EXTRACT", "8")
    monkeypatch.setenv("MC_MAX_BYTES_INLINE_TEMPLATE_EXTRACT", "1048576")
    monkeypatch.setenv("MC_MAX_LINES_INLINE_TEMPLATE_EXTRACT", "5000")
    a = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, work_dir_a, differential_report_enabled=True
    )
    monkeypatch.setenv("MC_DISABLE_INLINE_TEMPLATE_EXTRACT", "1")
    b = acceptance_hardening._measure_dataset_quick(
        dataset_root, spec, work_dir_b, differential_report_enabled=True
    )
    assert (
        a["methods"]["mc_final_selected"]["size"]
        == b["methods"]["mc_final_selected"]["size"]
    )
    assert (
        a["decision_report"]["selected_path"] == b["decision_report"]["selected_path"]
    )
