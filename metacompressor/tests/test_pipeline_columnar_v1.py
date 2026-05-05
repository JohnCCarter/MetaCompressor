"""Multi-stage column pipeline (pipeline_v1: string_pattern pool + chained stages)."""

from __future__ import annotations

from metacompressor.corpus_template import (
    _ADAPT_PIPELINE,
    _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2,
    _COLUMN_ENCODE_PROFILE_PIPELINE_V1,
    _decode_column,
    _encode_column_select,
    _msgpack_size,
    _try_chained_prefix_suffix_string_pattern,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def test_chained_prefix_suffix_string_pattern_round_trip():
    prefix = "https://api.example.com/v1/items/"
    suffix = "/detail.json"
    middles = [f"user-{i % 5}-segment" for i in range(120)]
    values = [prefix + m + suffix for m in middles]
    enc = _try_chained_prefix_suffix_string_pattern(values)
    assert enc is not None
    assert _decode_column(enc, len(values)) == values


def test_pipeline_profile_deterministic_two_runs():
    values = [
        f"INFO path=https://api.example.com/v1/x/{i}.json ok=1\n" for i in range(90)
    ]
    e1, m1 = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_PIPELINE_V1)
    e2, m2 = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_PIPELINE_V1)
    assert e1 == e2
    assert m1["winner"] == m2["winner"]


def test_mixed_corpus_pipeline_not_larger_than_string_pattern(tmp_path):
    body = b"".join(
        (
            f"INFO user={i % 9} path=https://api.example.com/v1/users/{i}/items.json "
            f"item={i} status={200 + (i % 3)}\n"
        ).encode()
        for i in range(260)
    )
    files = {"mix.log": body}
    corpus = make_corpus(tmp_path, files)
    _, m_sp = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+string_pattern"
    )
    _, m_pl = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+pipeline")
    sz_sp = m_sp["candidate_sizes"]["string_pattern_encoding_v1"]
    sz_pl = m_pl["candidate_sizes"][_ADAPT_PIPELINE]
    assert sz_pl <= sz_sp


def test_simple_corpus_no_regression_vs_v2_2(tmp_path):
    files = {"a.log": b"INFO n=1\nINFO n=2\n" * 120}
    corpus = make_corpus(tmp_path, files)
    _, m22 = compress_corpus_template_with_metrics(corpus, adaptive="v2.2")
    _, mpl = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+pipeline")
    assert mpl["compressed_size"] <= m22["compressed_size"] + 64


def test_pipeline_adaptive_round_trip(tmp_path):
    body = b"".join(
        (
            b"INFO path=https://cdn.example.com/v1/assets/img/%03d.png "
            b"status=200\n" % (i % 17,)
        )
        for i in range(200)
    )
    files = {"u.log": body}
    corpus = make_corpus(tmp_path, files)
    archive, _m = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+pipeline"
    )
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    assert (out / "u.log").read_bytes() == files["u.log"]


def test_chained_can_shrink_msgpack_vs_monolithic_prefix_suffix():
    prefix = "https://x.example.com/p/"
    suffix = "/tail"
    middles = [f"{i}/api/v1/item.json" for i in range(100)]
    values = [prefix + m + suffix for m in middles]
    ps_only, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    assert ps_only["encoding"] == "prefix_suffix_dictionary"
    pl_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_PIPELINE_V1)
    assert _decode_column(pl_enc, len(values)) == values
    assert _msgpack_size(pl_enc) <= _msgpack_size(ps_only)
