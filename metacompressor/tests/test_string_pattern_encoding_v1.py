"""String-pattern column encoding (string_pattern_v1 profile)."""

from __future__ import annotations

from metacompressor.corpus_template import (
    _ADAPT_STRING_PATTERN,
    _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1,
    _COLUMN_ENCODE_PROFILE_V2,
    _ENCODING_STRING_PATTERN_V1,
    _decode_column,
    _encode_column_select,
    _msgpack_size,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def test_string_pattern_round_trip_column():
    values = [
        f"{'GET' if i % 2 == 0 else 'POST'} /api/v1/users/{i}.json HTTP/1.1"
        for i in range(80)
    ]
    enc, meta = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1)
    assert enc["encoding"] == _ENCODING_STRING_PATTERN_V1
    assert _decode_column(enc, len(values)) == values
    assert meta["winner"] == _ENCODING_STRING_PATTERN_V1


def test_string_pattern_deterministic_two_runs():
    values = [
        f"INFO path=https://api.example.com/v1/x/{i}.json ok=1\n" for i in range(80)
    ]
    e1, m1 = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1)
    e2, m2 = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1)
    assert e1 == e2
    assert m1["winner"] == m2["winner"]


def test_high_entropy_hex_no_regression_vs_v2():
    values = [f"{i:032x}" for i in range(200)]
    sp_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1)
    v2_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _decode_column(sp_enc, len(values)) == values
    assert _msgpack_size(sp_enc) <= _msgpack_size(v2_enc)


def test_url_like_column_msgpack_beats_plain_v2():
    values = [
        f"INFO url=https://cdn.example.com/api/v1/assets/{i % 20}.json status=200\n"
        for i in range(200)
    ]
    sp_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1)
    v2_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _msgpack_size(sp_enc) < _msgpack_size(v2_enc)
    assert _decode_column(sp_enc, len(values)) == values


def test_mixed_fields_corpus_string_pattern_mck_vs_v2_2(tmp_path):
    body = b"".join(
        (
            f"INFO user={i % 9} path=https://api.example.com/v1/users/{i}/items.json "
            f"item={i} status={200 + (i % 3)}\n"
        ).encode()
        for i in range(180)
    )
    files = {"mix.log": body}
    corpus = make_corpus(tmp_path, files)
    _, m_v22 = compress_corpus_template_with_metrics(corpus, adaptive="v2.2")
    _, m_sp = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+string_pattern"
    )
    sz_v22 = m_v22["candidate_sizes"]["columnar_encoding_v2"]
    sz_sp = m_sp["candidate_sizes"][_ADAPT_STRING_PATTERN]
    assert sz_sp < sz_v22


def test_url_heavy_corpus_round_trip_adaptive_string_pattern(tmp_path):
    body = b"".join(
        (
            b"INFO path=https://cdn.example.com/v1/assets/img/%03d.png "
            b"status=200\n" % (i % 17,)
        )
        for i in range(400)
    )
    files = {"urls.log": body}
    corpus = make_corpus(tmp_path, files)
    archive, _m = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+string_pattern"
    )
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    assert (out / "urls.log").read_bytes() == files["urls.log"]
