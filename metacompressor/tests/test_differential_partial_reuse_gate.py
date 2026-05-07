from __future__ import annotations

from pathlib import Path

from metacompressor.differential import compress_corpus_differential


def _write_corpus(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


_SAMPLE = {
    "alpha.txt": b"A" * 4096,
    "beta.txt": b"B" * 4096,
}


def test_flag_off_baseline_behavior(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    result = compress_corpus_differential(corpus, cache)
    assert "partial_reuse_experiment_enabled" not in result.report
    assert "returned_archive_source" not in result.report


def test_flag_on_runs_verification_but_returns_fresh_archive(
    tmp_path: Path, monkeypatch
) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    result = compress_corpus_differential(corpus, cache)
    assert isinstance(result.archive, bytes)
    assert len(result.archive) > 0
    assert result.report["partial_reuse_experiment_enabled"] is True
    assert result.report["returned_archive_source"] == "fresh_full_build"


def test_fallback_reasons_have_deterministic_keyset(
    tmp_path: Path, monkeypatch
) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    result = compress_corpus_differential(corpus, cache)
    reasons = result.report["miss_reasons"]
    assert sorted(reasons.keys()) == sorted(
        [
            "manifest_changed",
            "chunk_hash_changed",
            "chunk_size_changed",
            "new_chunks",
            "deleted_chunks",
            "receipt_missing",
            "receipt_mismatch",
            "config_mismatch",
            "archive_missing",
            "low_confidence",
            "noisy_entropy_shift",
            "noisy_fail_closed",
            "deterministic_merge_violation",
            "real_decision_metadata_missing",
            "real_decision_metadata_unavailable",
            "strategy_encoding_real_mismatch",
            "byte_parity_mismatch",
            "artifact_missing",
            "artifact_schema_invalid",
            "artifact_hash_mismatch",
            "runtime_strategy_mismatch",
            "runtime_substitution_parity_mismatch",
            "runtime_replay_nondeterministic",
        ]
    )
    assert all(isinstance(v, int) for v in reasons.values())


def test_merge_validation_helper_detects_overlap() -> None:
    from metacompressor.differential.core import ChunkFingerprint, Manifest
    from metacompressor.differential.orchestrator import (
        _validate_simulated_selective_candidate,
    )

    manifest = Manifest(
        schema_version=1,
        chunk_size_bytes=1024,
        chunks=(
            ChunkFingerprint(
                chunk_id="a::00000000",
                relative_path="a",
                chunk_index=0,
                size_bytes=10,
                chunk_hash="aa",
            ),
        ),
    )
    ok, reason = _validate_simulated_selective_candidate(
        manifest, ("a::00000000",), ("a::00000000",)
    )
    assert ok is False
    assert reason == "deterministic_merge_violation"


def test_merge_violation_fails_closed_in_report(tmp_path: Path, monkeypatch) -> None:
    import metacompressor.differential.orchestrator as orch

    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    monkeypatch.setattr(
        orch,
        "_validate_simulated_selective_candidate",
        lambda *args, **kwargs: (False, "deterministic_merge_violation"),
    )
    result = compress_corpus_differential(corpus, cache)
    assert result.report["fail_closed"] is True
    assert result.report["reason"] == "deterministic_merge_violation"
    assert result.report["cache_hit_candidate"] is False
    assert result.report["miss_reasons"]["deterministic_merge_violation"] >= 1


def test_report_completeness_fields_present(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    result = compress_corpus_differential(corpus, cache)
    required = {
        "partial_reuse_experiment_enabled",
        "verification_mode",
        "returned_archive_source",
        "byte_identical_parity_pass",
        "strategy_encoding_real_match_pass",
        "deterministic_merge_pass",
        "noisy_fail_closed_pass",
        "real_decision_metadata_used",
        "fallback_reason_counts",
        "gates_evaluated",
        "gates_failed",
        "runtime_substitution_enabled",
        "runtime_substitution_attempted",
        "runtime_substitution_used",
        "runtime_substitution_fail_reason",
        "runtime_substitution_candidate_equal_fresh",
        "runtime_replay_deterministic",
        "runtime_substitution_time_ms",
        "runtime_validation_overhead_ms",
        "runtime_substitution_reused_chunks",
        "runtime_substitution_rebuilt_chunks",
    }
    assert required.issubset(result.report.keys())
    assert result.report["returned_archive_source"] == "fresh_full_build"


def test_parity_mismatch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    (corpus / "alpha.txt").write_bytes(b"Z" * 4096)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    result = compress_corpus_differential(corpus, cache)
    assert result.report["byte_identical_parity_pass"] is False
    assert result.report["fail_closed"] is True
    assert result.report["reason"] == "byte_parity_mismatch"
    assert result.report["fallback_reason_counts"]["byte_parity_mismatch"] >= 1


def test_strategy_mismatch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import metacompressor.differential.orchestrator as orch

    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    monkeypatch.setattr(
        orch,
        "_compute_real_decision_metadata",
        lambda *args, **kwargs: {
            "selected_mode": "adaptive_rc2",
            "column_encoding_counts": {"row_template_v1": 1},
        },
    )
    compress_corpus_differential(corpus, cache)
    result = compress_corpus_differential(corpus, cache)
    assert result.report["strategy_encoding_real_match_pass"] is True
    # simulate mismatch by forcing old-vs-new metadata divergence
    monkeypatch.setattr(
        orch,
        "_extract_real_decision_metadata",
        lambda *args, **kwargs: {
            "selected_mode": "adaptive_rc2",
            "column_encoding_counts": {"row_template_v1": 1},
        },
    )
    monkeypatch.setattr(
        orch,
        "_compute_real_decision_metadata",
        lambda *args, **kwargs: {
            "selected_mode": "adaptive_rc2",
            "column_encoding_counts": {"row_template_v1": 2},
        },
    )
    result2 = compress_corpus_differential(corpus, cache)
    assert result2.report["strategy_encoding_real_match_pass"] is False
    assert result2.report["fail_closed"] is True
    assert result2.report["reason"] == "strategy_encoding_real_mismatch"
    assert (
        result2.report["fallback_reason_counts"]["strategy_encoding_real_mismatch"] >= 1
    )


def test_real_decision_metadata_used_true_when_flag_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    result = compress_corpus_differential(corpus, cache)
    assert result.report["real_decision_metadata_used"] is True


def test_real_decision_metadata_computed_once_per_run(
    tmp_path: Path, monkeypatch
) -> None:
    import metacompressor.differential.orchestrator as orch

    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    calls = {"count": 0}

    def _counted_metadata(*args, **kwargs):
        calls["count"] += 1
        return {
            "selected_mode": "adaptive_rc2",
            "column_encoding_counts": {"row_template_v1": 1},
        }

    monkeypatch.setattr(orch, "_compute_real_decision_metadata", _counted_metadata)
    compress_corpus_differential(corpus, cache)
    assert calls["count"] == 1


def test_noisy_gate_fail_closed(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    for i in range(5):
        (corpus / "alpha.txt").write_bytes((f"{i}" * 4096).encode("utf-8"))
        (corpus / "beta.txt").write_bytes((f"x{i}" * 4096).encode("utf-8"))
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT", "1")
    result = compress_corpus_differential(corpus, cache)
    assert result.report["miss_reasons"]["noisy_entropy_shift"] in (0, 1)
    if result.report["reason"] == "noisy_fail_closed":
        assert result.report["noisy_fail_closed_pass"] is True
        assert result.report["fallback_reason_counts"]["noisy_fail_closed"] >= 1


def test_runtime_substitution_experimental_still_returns_fresh(
    tmp_path: Path, monkeypatch
) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_RUNTIME", "1")
    result = compress_corpus_differential(corpus, cache)
    assert result.report["runtime_substitution_enabled"] is True
    assert result.report["runtime_substitution_attempted"] is True
    assert result.report["returned_archive_source"] == "fresh_full_build"
    assert isinstance(result.archive, bytes)
    assert len(result.archive) > 0


def test_runtime_substitution_noisy_fail_closed(tmp_path: Path, monkeypatch) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    for i in range(8):
        (corpus / "alpha.txt").write_bytes((f"a{i}" * 4096).encode("utf-8"))
        (corpus / "beta.txt").write_bytes((f"b{i}" * 4096).encode("utf-8"))
    monkeypatch.setenv("MC_ENABLE_PARTIAL_REUSE_RUNTIME", "1")
    result = compress_corpus_differential(corpus, cache)
    assert result.report["runtime_substitution_enabled"] is True
    assert result.report["fail_closed"] is True
    assert result.report["reason"] in (
        "noisy_fail_closed",
        "runtime_substitution_parity_mismatch",
        "runtime_strategy_mismatch",
        "byte_parity_mismatch",
    )
