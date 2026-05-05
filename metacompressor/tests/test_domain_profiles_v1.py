"""Domain profile behavior on top of existing strategies."""

from __future__ import annotations

from typing import Dict, List

from metacompressor.corpus_template import (
    _rank_v23_candidates,
    compress_corpus_template_with_metrics,
)
from metacompressor.tests.test_corpus_template import make_corpus


def _baseline_v23_rank(profile: str, scores: Dict[str, float]) -> List[str]:
    row_score = float(scores.get("row_template", 1.0))
    col_score = float(scores.get("columnar_encoding_v2", 1.0))
    ranking = [
        (row_score, "row_template"),
        (col_score, "columnar_encoding_v2"),
        (col_score + 0.004, "field_aware_columnar_v2"),
        (col_score + 0.005, "string_pattern_v1"),
        (col_score + 0.006, "pipeline_columnar_v1"),
        (col_score + 0.008, "relational_encoding_v1"),
    ]
    if profile == "nginx":
        ranking = [
            (
                score
                - (0.030 if mode == "string_pattern_v1" else 0.0)
                - (0.022 if mode == "field_aware_columnar_v2" else 0.0),
                mode,
            )
            for score, mode in ranking
        ]
    elif profile == "logs":
        ranking = [
            (
                score
                - (0.026 if mode == "field_aware_columnar_v2" else 0.0)
                - (0.024 if mode == "pipeline_columnar_v1" else 0.0),
                mode,
            )
            for score, mode in ranking
        ]
    ranking.sort(key=lambda item: (item[0], item[1]))
    return [mode for _score, mode in ranking]


def test_profile_deterministic_per_profile(tmp_path):
    files = {"a.log": b"INFO n=1\nINFO n=2\n" * 120}
    corpus = make_corpus(tmp_path, files)
    for profile in ("generic", "logs", "nginx", "json"):
        a1, m1 = compress_corpus_template_with_metrics(
            corpus, adaptive="v2.2+pipeline", profile=profile
        )
        a2, m2 = compress_corpus_template_with_metrics(
            corpus, adaptive="v2.2+pipeline", profile=profile
        )
        assert a1 == a2
        assert m1["selected_mode"] == m2["selected_mode"]
        assert m1["selected_profile"] == profile


def test_profile_worst_loss_guard_unchanged(tmp_path):
    files = {f"u{i}.log": f"line-{i}\n".encode() for i in range(40)}
    corpus = make_corpus(tmp_path, files)
    for profile in ("generic", "logs", "nginx", "json"):
        _, metrics = compress_corpus_template_with_metrics(
            corpus, adaptive="v2.2+pipeline", profile=profile
        )
        assert metrics["compressed_size"] <= int(metrics["tarzstd_size"] * 1.10) + 64


def test_logs_profile_not_worse_on_mixed_dataset(tmp_path):
    body = b"".join(
        (
            f"INFO user={i % 9} path=https://api.example.com/v1/users/{i}/items.json "
            f"item={i} trace=/api/v1/events/{i % 12}.json status={200 + (i % 3)}\n"
        ).encode()
        for i in range(300)
    )
    corpus = make_corpus(tmp_path, {"mix.log": body})
    _, m_generic = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+pipeline", profile="generic"
    )
    _, m_logs = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+pipeline", profile="logs"
    )
    assert m_logs["compressed_size"] <= m_generic["compressed_size"]
    assert m_logs["strategy_weights_used"]["feature_weights"]["structure_score"] >= 1.0


def test_v23_profiles_change_ranked_mode(tmp_path):
    body = b"".join(
        (
            f"INFO method={'GET' if i % 2 else 'POST'} "
            f"path=/api/v1/orders/{i % 21}/item.json "
            f"status={200 + (i % 4)} trace={i:06d}\n"
        ).encode()
        for i in range(420)
    )
    corpus = make_corpus(tmp_path, {"p.log": body})
    _, m_logs = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.3", profile="logs"
    )
    _, m_nginx = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.3", profile="nginx"
    )
    r_logs = ((m_logs.get("predictive_v2") or {}).get("v23") or {}).get(
        "ranked_candidates", []
    )
    r_nginx = ((m_nginx.get("predictive_v2") or {}).get("v23") or {}).get(
        "ranked_candidates", []
    )
    assert r_logs and r_nginx
    assert r_logs[0] != r_nginx[0]


def test_v23_mixed_does_not_fallback_to_tar_if_ranked_candidates_exist(tmp_path):
    body = b"".join(
        (
            f"INFO user={i % 9} path=https://api.example.com/v1/users/{i}/items.json "
            f"item={i} trace=/api/v1/events/{i % 12}.json status={200 + (i % 3)}\n"
        ).encode()
        for i in range(300)
    )
    corpus = make_corpus(tmp_path, {"mix.log": body})
    _, metrics = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.3", profile="logs"
    )
    v23_meta = (metrics.get("predictive_v2") or {}).get("v23") or {}
    assert v23_meta.get("ranked_candidates")
    assert metrics["selected_mode"] != "raw_tar_zstd"


def test_v23_predictor_logs_feature_values_and_strategy_scores(tmp_path):
    body = b"".join(
        (
            f"INFO user={i % 9} path=/api/v1/orders/{i % 15}/item-{i:03d}.json "
            f"trace=trace-{i:05d} status={200 + (i % 3)}\n"
        ).encode()
        for i in range(380)
    )
    corpus = make_corpus(tmp_path, {"mix.log": body})
    _, metrics = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.3", profile="logs"
    )
    pred = metrics["predictive_v2"]
    v23 = pred["v23"]
    fv = pred["feature_values"]
    assert set(fv.keys()) == {
        "token_reuse_ratio",
        "average_token_length",
        "prefix_similarity_score",
        "field_variance_score",
    }
    for key, value in fv.items():
        assert isinstance(value, float), key
    assert "strategy_scores" in v23
    assert any("string_pattern" in k for k in v23["strategy_scores"])
    assert "field_aware_columnar_v2" in v23["strategy_scores"]
    assert "columnar_encoding_v2" in v23["strategy_scores"]


def test_v23_top1_accuracy_improves_vs_baseline(tmp_path):
    del tmp_path
    scenarios = [
        {
            "profile": "nginx",
            "oracle": "string_pattern_columnar_v1",
            "scores": {"row_template": 1.03, "columnar_encoding_v2": 1.00},
            "features": {
                "structure_stability": 0.80,
                "prefix_similarity_score": 0.92,
                "average_token_length": 15.0,
                "field_variance_score": 0.42,
            },
        },
        {
            "profile": "nginx",
            "oracle": "field_aware_columnar_v2",
            "scores": {"row_template": 1.02, "columnar_encoding_v2": 1.00},
            "features": {
                "structure_stability": 0.93,
                "prefix_similarity_score": 0.35,
                "average_token_length": 9.5,
                "field_variance_score": 0.46,
            },
        },
        {
            "profile": "json",
            "oracle": "columnar_encoding_v2",
            "scores": {"row_template": 1.02, "columnar_encoding_v2": 1.00},
            "features": {
                "structure_stability": 0.95,
                "prefix_similarity_score": 0.30,
                "average_token_length": 6.5,
                "field_variance_score": 0.08,
            },
        },
    ]
    baseline_hits = 0
    improved_hits = 0
    for item in scenarios:
        baseline_top1 = _baseline_v23_rank(item["profile"], item["scores"])[0]
        ranked, _strategy_scores = _rank_v23_candidates(
            profile=item["profile"],
            prediction_scores=item["scores"],
            predictor_features=item["features"],
        )
        improved_top1 = ranked[0]
        if baseline_top1 == item["oracle"]:
            baseline_hits += 1
        if improved_top1 == item["oracle"]:
            improved_hits += 1
    assert improved_hits > baseline_hits
