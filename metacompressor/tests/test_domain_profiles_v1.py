"""Domain profile behavior on top of existing strategies."""

from __future__ import annotations

from metacompressor.corpus_template import compress_corpus_template_with_metrics
from metacompressor.tests.test_corpus_template import make_corpus


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
