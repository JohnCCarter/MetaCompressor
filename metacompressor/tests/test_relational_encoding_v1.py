"""Relational cross-field tuple encoding (relational_encoding_v1)."""

from __future__ import annotations

from metacompressor.corpus_template import (
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def test_relational_round_trip_with_correlated_fields(tmp_path):
    lines = []
    for i in range(320):
        method = "GET" if i % 4 else "POST"
        path_prefix = f"/api/v1/orders/{i % 16}"
        status = 200 if i % 6 else 500
        line = (
            f"service=orders level=INFO method={method} path={path_prefix}/item.json "
            f"status={status} code={100 + (i % 5)}\n"
        )
        lines.append(line.encode())
    corpus = make_corpus(tmp_path, {"corr.log": b"".join(lines)})
    archive, _metrics = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+relational"
    )
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    assert (out / "corr.log").read_bytes() == b"".join(lines)


def test_relational_deterministic_two_runs(tmp_path):
    body = b"".join(
        (
            f"method={'GET' if i % 2 else 'POST'} "
            f"path=/api/v1/users/{i % 12}.json status={200 + (i % 3)}\n"
        ).encode()
        for i in range(260)
    )
    corpus = make_corpus(tmp_path, {"d.log": body})
    a1, m1 = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+relational")
    a2, m2 = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+relational")
    assert a1 == a2
    assert m1["selected_mode"] == m2["selected_mode"]


def test_relational_correlated_improves_vs_pipeline(tmp_path):
    combos = [
        ("GET", "/api/v1/payments/create.json", "200", "0"),
        ("GET", "/api/v1/payments/create.json", "429", "77"),
        ("POST", "/api/v1/payments/retry.json", "200", "0"),
        ("POST", "/api/v1/payments/retry.json", "500", "11"),
        ("DELETE", "/api/v1/payments/cancel.json", "200", "0"),
        ("DELETE", "/api/v1/payments/cancel.json", "404", "51"),
    ]
    body = b"".join(
        (
            f"svc=checkout level=INFO method={combos[i % len(combos)][0]} "
            f"endpoint={combos[i % len(combos)][1]} "
            f"status={combos[i % len(combos)][2]} err={combos[i % len(combos)][3]} "
            f"req={i:05d}\n"
        ).encode()
        for i in range(420)
    )
    corpus = make_corpus(tmp_path, {"mix.log": body})
    _, m_pipe = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+pipeline")
    _, m_rel = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+relational")
    assert m_rel["compressed_size"] <= m_pipe["compressed_size"]
    rel_meta = m_rel.get("relational_encoding_v1") or {}
    assert "estimated_gain" in rel_meta
    assert "actual_gain" in rel_meta


def test_relational_rejects_uncorrelated_random_fields(tmp_path):
    body = b"".join(
        (
            f"method={i:08x} path=/x/{i:08x} status={100000 + i} code={200000 + i}\n"
        ).encode()
        for i in range(220)
    )
    corpus = make_corpus(tmp_path, {"rand.log": body})
    _, m_rel = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+relational")
    rel_meta = m_rel.get("relational_encoding_v1") or {}
    assert rel_meta.get("applied_count", 0) == 0


def test_relational_tar_fallback_still_bounds_worst_case(tmp_path):
    files = {f"u{i}.log": f"line-{i}\n".encode() for i in range(48)}
    corpus = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+relational"
    )
    assert metrics["compressed_size"] <= int(metrics["tarzstd_size"] * 1.10) + 64
