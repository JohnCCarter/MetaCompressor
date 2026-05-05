"""Unit tests for corpus-template column encoding (v2 selection vs v1 profile)."""

from __future__ import annotations

from metacompressor.corpus_template import (
    _COLUMN_ENCODE_PROFILE_V1,
    _COLUMN_ENCODE_PROFILE_V2,
    _decode_column,
    _encode_column_select,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def test_encode_select_dictionary_round_trip():
    values = ["a", "b", "a", "b", "a"] * 10
    enc, meta = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert meta["candidates_tried"] >= 1
    assert _decode_column(enc, len(values)) == values


def test_encode_select_rle_round_trip():
    values = ["x"] * 50
    enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _decode_column(enc, len(values)) == values


def test_encode_select_delta_varint_round_trip():
    values = [str(i) for i in range(100)]
    enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _decode_column(enc, len(values)) == values


def test_encode_select_preserves_leading_zero_strings():
    values = ["0012", "0013", "0014"]
    enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert enc["encoding"] == "raw_msgpack"
    assert _decode_column(enc, len(values)) == values


def test_encode_select_preserves_float_string_formatting():
    values = ["1.0", "2.0", "3.0"]
    enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    out = _decode_column(enc, len(values))
    assert out == values


def test_v2_profile_can_beat_v1_on_repeated_strings():
    values = ["same"] * 200
    v1_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V1)
    v2_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    from metacompressor.corpus_template import _msgpack_size

    assert _msgpack_size(v2_enc) < _msgpack_size(v1_enc)
    assert v2_enc["encoding"] in ("dictionary", "rle")


def test_prefixed_ndjson_corpus_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "metacompressor.corpus_template._CORPUS_FALLBACK_THRESHOLD",
        float("inf"),
    )
    line = b'{"service":"api","status":200,"request_id":"user-%d","path":"/p"}\n'
    body = b"".join(b"2026-05-04T12:00:00Z " + (line % i) for i in range(40))
    corpus_dir = make_corpus(tmp_path, {"p.ndjson": body})
    archive, metrics = compress_corpus_template_with_metrics(corpus_dir)
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    assert (out / "p.ndjson").read_bytes() == body
    assert metrics.get("columnar_v2_savings_vs_v1_columns", 0) >= 0


def test_metrics_include_columnar_v2_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "metacompressor.corpus_template._CORPUS_FALLBACK_THRESHOLD",
        float("inf"),
    )
    files = {
        "a.log": b"".join(
            f"INFO seq={i} status={i % 5} user={i % 9}\n".encode() for i in range(200)
        ),
    }
    corpus_dir = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus_dir)
    assert metrics["columnar_v2_enabled"] is True
    assert "column_encoding_candidates" in metrics
    assert "column_encoding_selected_counts" in metrics
    assert "columnar_v1_size" in metrics
    assert metrics["columnar_v1_size"] > 0
    assert metrics["columnar_size"] > 0
