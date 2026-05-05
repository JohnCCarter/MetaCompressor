"""Predictive adaptive mode selection v2 (opt-in via ``adaptive=\"v2\"``)."""

from __future__ import annotations

import time

import pytest

from metacompressor.corpus_template import (
    _ADAPT_COL_V2,
    _ADAPT_HYBRID,
    _ADAPT_ROW,
    _MODE_COLUMNAR_V2,
    _MODE_HYBRID_ROW_COLUMNAR_V1,
    _MODE_ROW_V1,
    _adaptive_v2_pick,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def test_v2_zero_reuse_dataset_predicts_tar_and_stays_within_tolerance(tmp_path):
    """Unique-line corpus: predictor ranks TAR first; output never blows past TAR."""
    files = {
        f"f{i:03d}.log": f"unique-{i}-{j}\n".encode()
        for i in range(30)
        for j in range(1)
    }
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2")
    assert metrics["adaptive_version"] == "v2"
    pred = metrics["predictive_v2"]
    assert pred is not None
    assert pred["primary_build"] == "raw_tar_zstd"
    tar_mck = metrics["candidate_sizes"].get("raw_tar_zstd", metrics["compressed_size"])
    assert metrics["compressed_size"] <= int(tar_mck * 1.03)


def test_v2_high_repetition_prefers_template_not_tar(tmp_path):
    body = b"INFO seq=1 status=200 path=/ok\n" * 800
    files = {"app.log": body}
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2")
    assert metrics["predictive_v2"]["skipped_template_builds"] is False
    assert metrics["selected_mode"] in (_ADAPT_ROW, _ADAPT_COL_V2)
    assert metrics["final_selected_mode"] in (_MODE_ROW_V1, _MODE_COLUMNAR_V2)


def test_v2_high_cardinality_prefers_row_over_columnar(tmp_path):
    """Many unique URL slots penalize columnar score vs row."""
    lines = [
        b"INFO url=https://example.com/item/%032x\n" % (i * 7919,) for i in range(400)
    ]
    files = {"urls.log": b"".join(lines)}
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2")
    pred = metrics["predictive_v2"]
    assert pred["scores"]["row_template"] <= pred["scores"]["columnar_encoding_v2"]


def test_v21_expected_score_confidence_and_error_metrics(tmp_path):
    files = {"app.log": b"INFO seq=1 status=200 path=/ok\n" * 500}
    corpus = make_corpus(tmp_path, files)
    archive, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2.1")
    pred = metrics["predictive_v2"]

    assert metrics["adaptive_version"] == "v2.1"
    assert (
        pred["expected_compression_score"] == pred["scores"][metrics["selected_mode"]]
    )
    assert (
        pred["score_components"][metrics["selected_mode"]]["metadata_overhead_penalty"]
        >= 0
    )
    assert pred["confidence"] >= 0
    assert pred["error"] == metrics["compressed_size"] - pred["predicted_size"]

    out = tmp_path / "v21_out"
    decompress_corpus_template(archive, out)
    assert (out / "app.log").read_bytes() == files["app.log"]


def test_v21_low_confidence_builds_two_candidates_when_scores_are_close(tmp_path):
    files = {
        "mix.log": b"".join(
            f"INFO user={i % 9} item={i} status={200 + (i % 3)}\n".encode()
            for i in range(120)
        )
    }
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2.1")
    pred = metrics["predictive_v2"]
    if pred["verify_second_template"]:
        built_templates = [
            key
            for key in ("row_template", "columnar_encoding_v2")
            if key in metrics["candidate_sizes"]
        ]
        assert len(built_templates) == 2


def test_v21_confidence_aware_aggression_metrics(tmp_path):
    files = {
        "mixed.log": b"".join(
            f"INFO item={i} user={i % 7}\n".encode() for i in range(160)
        )
    }
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(
        corpus,
        adaptive="v2.1",
        aggression_factor=1.4,
    )
    pred = metrics["predictive_v2"]
    assert pred["aggression_factor"] == 1.4
    assert pred["confidence_band"] in {"high", "low", "risk"}
    assert pred["score_gap"] == pred["confidence"]
    assert isinstance(pred["skip_tar_guard"], bool)


def test_v22_structure_aware_prefers_columnar_for_stable_mixed_dataset(tmp_path):
    files = {
        "events.log": b"".join(
            (
                "INFO user=%d item=%d status=%d region=%s route=/api/%d "
                "latency_ms=%d\n"
            ).encode()
            % (
                i % 17,
                i,
                200 + (i % 5),
                [b"iad", b"sfo", b"fra", b"sin"][i % 4],
                i % 11,
                (i * 7) % 250,
            )
            for i in range(600)
        )
    }
    corpus = make_corpus(tmp_path, files)
    archive, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2.2")
    pred = metrics["predictive_v2"]

    assert metrics["adaptive_version"] == "v2.2"
    assert metrics["selected_mode"] == _ADAPT_COL_V2
    assert pred["primary_build"] == _ADAPT_COL_V2
    assert pred["structure_signal_strong"] is True
    assert pred["model_quality"] >= 0.72

    out = tmp_path / "v22_out"
    decompress_corpus_template(archive, out)
    assert (out / "events.log").read_bytes() == files["events.log"]


def test_v22_structure_score_metrics_are_sane(tmp_path):
    stable_files = {
        "stable.log": b"".join(
            f"INFO user={i % 3} status={200 + (i % 2)} route=/ok\n".encode()
            for i in range(80)
        )
    }
    noisy_files = {
        "noisy.log": b"".join(
            (
                f"INFO field{chr(97 + (i % 26))}=v{i} "
                f"alt{chr(97 + ((i // 3) % 26))}=x{i} "
                f"status={200 + (i % 5)}\n"
            ).encode()
            for i in range(80)
        )
    }

    _, stable_metrics = compress_corpus_template_with_metrics(
        make_corpus(tmp_path / "stable", stable_files),
        adaptive="v2.2",
    )
    _, noisy_metrics = compress_corpus_template_with_metrics(
        make_corpus(tmp_path / "noisy", noisy_files),
        adaptive="v2.2",
    )
    stable_pred = stable_metrics["predictive_v2"]
    noisy_pred = noisy_metrics["predictive_v2"]

    assert 0.0 <= stable_pred["structure_score"] <= 1.0
    assert 0.0 <= noisy_pred["structure_score"] <= 1.0
    assert stable_pred["structure_score"] < 0.05
    assert noisy_pred["structure_score"] > stable_pred["structure_score"]
    assert stable_pred["prediction_confidence"] == stable_pred["score_gap"]
    assert "model_quality" in stable_pred


def test_v2_many_small_files_round_trip(tmp_path):
    files = {f"shard/{i:04d}.log": b"OK row=1\n" for i in range(40)}
    corpus = make_corpus(tmp_path, files)
    archive, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2")
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    for rel, data in files.items():
        assert (out / rel).read_bytes() == data
    assert metrics["adaptive_version"] == "v2"


def test_hybrid_v22_plus_round_trip_lossless(tmp_path):
    files = {
        "events.log": b"".join(
            (
                b"INFO user=%d item=%d status=%d region=%s route=/api/%d "
                b"latency_ms=%d\n"
            )
            % (
                i % 17,
                i,
                200 + (i % 5),
                [b"iad", b"sfo", b"fra", b"sin"][i % 4],
                i % 11,
                (i * 7) % 250,
            )
            for i in range(120)
        )
    }
    corpus = make_corpus(tmp_path, files)
    archive, metrics = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+hybrid"
    )
    assert metrics["adaptive_version"] == "v2.2+hybrid"
    hy = metrics.get("hybrid_row_columnar_v1")
    assert hy is not None
    assert hy["eligible"] is True
    assert hy["structure_score"] == metrics["predictive_v2"]["structure_score"]

    out = tmp_path / "hy_out"
    decompress_corpus_template(archive, out)
    assert (out / "events.log").read_bytes() == files["events.log"]


def test_hybrid_v22_plus_deterministic_two_runs(tmp_path):
    files = {"a.log": b"INFO n=1\nINFO n=2\n" * 80}
    corpus = make_corpus(tmp_path, files)
    a1, m1 = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+hybrid")
    a2, m2 = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+hybrid")
    assert a1 == a2
    assert m1["selected_mode"] == m2["selected_mode"]


def test_hybrid_ineligible_when_predictor_skips_columnar_build(tmp_path):
    """High-entropy / low-reuse: v2.2 may build only row; hybrid is not built."""
    files = {f"u{i}.log": f"line-{i}\n".encode() for i in range(35)}
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+hybrid")
    hy = metrics["hybrid_row_columnar_v1"]
    assert hy["eligible"] is False
    assert metrics["selected_mode"] != _ADAPT_HYBRID
    assert hy["eligibility_reason"] == "columnar_not_built_by_predictor"


def test_hybrid_eligible_on_mixed_structured_logs(tmp_path):
    files = {
        "mix.log": b"".join(
            f"INFO user={i % 9} item={i} status={200 + (i % 3)}\n".encode()
            for i in range(180)
        )
    }
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+hybrid")
    hy = metrics["hybrid_row_columnar_v1"]
    assert hy["eligible"] is True
    assert hy["eligibility_reason"] == "built_with_columnar_v2_prediction_pool"


def test_hybrid_fallback_respects_tar_size_guard(tmp_path):
    files = {"app.log": b"INFO seq=1 status=200 path=/ok\n" * 400}
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+hybrid")
    tar = int(metrics["tarzstd_size"])
    assert int(metrics["compressed_size"]) <= int(tar * 1.11) + 64


def test_adaptive_v2_pick_prefers_hybrid_on_size_tie_with_columnar():
    """Tie-break order: hybrid (1) before columnar v2 (2) when lengths tie."""
    tarzstd = bytes((i * 17 + i // 3) & 255 for i in range(200_000))
    row = b"r" * 300
    col = b"c" * 150
    hyb = b"h" * 150
    data, mode, raw, meta, _fb = _adaptive_v2_pick(
        tarzstd_bytes=tarzstd,
        tarzstd_size=len(tarzstd),
        tolerance_vs_tar=10.0,
        row_pack=(row, {"fallback_reason_counts": {}}),
        columnar_v2_pack=(col, {"fallback_reason_counts": {}}),
        hybrid_pack=(hyb, {"fallback_reason_counts": {}}),
    )
    assert meta["selected_mode"] == _ADAPT_HYBRID
    assert mode == _MODE_HYBRID_ROW_COLUMNAR_V1
    assert raw is False
    assert data == hyb


def test_adaptive_v2_pick_hybrid_strictly_smaller_than_row_and_columnar():
    """Synthetic sizes: hybrid beats both row and columnar candidates."""
    tarzstd = bytes((i * 17 + i // 3) & 255 for i in range(200_000))
    row = b"r" * 500
    col = b"c" * 300
    hyb = b"h" * 200
    data, mode, raw, meta, _fb = _adaptive_v2_pick(
        tarzstd_bytes=tarzstd,
        tarzstd_size=len(tarzstd),
        tolerance_vs_tar=10.0,
        row_pack=(row, {"fallback_reason_counts": {}}),
        columnar_v2_pack=(col, {"fallback_reason_counts": {}}),
        hybrid_pack=(hyb, {"fallback_reason_counts": {}}),
    )
    assert meta["selected_mode"] == _ADAPT_HYBRID
    assert mode == _MODE_HYBRID_ROW_COLUMNAR_V1
    assert len(data) < len(row) and len(data) < len(col)


@pytest.mark.parametrize(
    "adaptive",
    [
        "v1",
        "v2",
        "v2.1",
        "v2.2",
        "v2.2+hybrid",
        "v2.2+field_aware",
        "v2.2+string_pattern",
        "v2.2+pipeline",
        "v2.2+relational",
        "v2.3",
    ],
)
def test_v2_deterministic_same_as_second_run(tmp_path, adaptive):
    files = {"a.log": b"INFO n=1\nINFO n=2\n" * 120}
    corpus = make_corpus(tmp_path, files)
    a1, m1 = compress_corpus_template_with_metrics(corpus, adaptive=adaptive)
    a2, m2 = compress_corpus_template_with_metrics(corpus, adaptive=adaptive)
    assert a1 == a2
    assert m1["selected_mode"] == m2["selected_mode"]


def test_v2_encode_time_less_or_equal_than_v1_on_skippable_corpus(tmp_path):
    """When v2 skips template builds, encode phase should be faster than v1."""
    files = {f"u{i}.log": f"only-{i}\n".encode() for i in range(40)}
    corpus = make_corpus(tmp_path, files)
    t0 = time.perf_counter()
    _, m_v2 = compress_corpus_template_with_metrics(corpus, adaptive="v2")
    dt_v2 = time.perf_counter() - t0
    t1 = time.perf_counter()
    _, m_v1 = compress_corpus_template_with_metrics(corpus, adaptive="v1")
    dt_v1 = time.perf_counter() - t1
    if m_v2["predictive_v2"]["skipped_template_builds"]:
        assert m_v2["timing"]["encode_s"] <= m_v1["timing"]["encode_s"] + 1e-6
        assert dt_v2 < dt_v1 * 0.95 or m_v2["timing"]["encode_s"] == 0.0


def test_v23_universal_tar_size_guard_limits_small_dataset_losses(tmp_path):
    epsilon = 0.02
    datasets = [
        {
            "name": "small_json",
            "files": {
                "events.jsonl": b"".join(
                    (
                        '{"service":"auth","status":200,'
                        f'"route":"/token/{i % 7}","user":{i % 19}}}\n'
                    ).encode()
                    for i in range(620)
                )
            },
            "profile": "json",
        },
        {
            "name": "small_logs",
            "files": {
                "app.log": b"".join(
                    (
                        f"INFO user={i % 9} route=/api/{i % 11} "
                        f"status={200 + (i % 3)}\n"
                    ).encode()
                    for i in range(720)
                )
            },
            "profile": "logs",
        },
        {
            "name": "small_nginx_like",
            "files": {
                "access.log": b"".join(
                    (
                        f"10.0.0.{i % 250} - - [05/May/2026:12:{i % 60:02d}:00 +0000] "
                        f'"GET /v1/{i % 13}/item.json HTTP/1.1" {200 + (i % 4)} 1234\n'
                    ).encode()
                    for i in range(720)
                )
            },
            "profile": "nginx",
        },
    ]
    losses = []
    for idx, ds in enumerate(datasets):
        corpus = make_corpus(tmp_path / f"small_{idx}", ds["files"])
        _, metrics = compress_corpus_template_with_metrics(
            corpus,
            adaptive="v2.3",
            profile=ds["profile"],
        )
        loss = max(
            0.0,
            100.0
            * (int(metrics["compressed_size"]) - int(metrics["tarzstd_size"]))
            / max(1, int(metrics["tarzstd_size"])),
        )
        losses.append(loss)
        assert (
            int(metrics["compressed_size"])
            <= int(metrics["tarzstd_size"] * (1.0 + epsilon)) + 2
        )
        if metrics["fallback_triggered"]:
            assert metrics["fallback_reason"] == "container_overhead_guard"
    assert max(losses) <= 2.0
