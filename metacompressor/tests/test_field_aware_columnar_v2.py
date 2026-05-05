"""Field-aware column encodings (field_aware_v2 profile)."""

from __future__ import annotations

from metacompressor.corpus_template import (
    _ADAPT_FIELD_AWARE,
    _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2,
    _COLUMN_ENCODE_PROFILE_V2,
    _ENCODING_PREFIX_SUFFIX_DICTIONARY,
    _ENCODING_TIMESTAMP_STRING_DELTA,
    _ENCODING_URL_PATH_PREFIX,
    _decode_column,
    _encode_column_select,
    _msgpack_size,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def test_prefix_suffix_round_trip():
    values = [
        "https://api.example.com/v1/items/100/detail",
        "https://api.example.com/v1/items/101/detail",
        "https://api.example.com/v1/items/102/detail",
    ]
    enc, meta = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    assert enc["encoding"] == _ENCODING_PREFIX_SUFFIX_DICTIONARY
    assert _decode_column(enc, len(values)) == values
    assert meta["winner"] == _ENCODING_PREFIX_SUFFIX_DICTIONARY


def test_url_path_prefix_round_trip():
    values = [
        "https://cdn.example.com/assets/img/a.png",
        "https://cdn.example.com/assets/img/b.png",
        "https://cdn.example.com/assets/img/c.png",
    ]
    enc, _meta = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    assert enc["encoding"] == _ENCODING_URL_PATH_PREFIX
    assert _decode_column(enc, len(values)) == values


def test_timestamp_string_delta_round_trip():
    values = [
        "2024-01-01 00:00:00",
        "2024-01-01 00:00:01",
        "2024-01-01 00:00:02",
        "2024-01-01 00:00:03",
    ]
    enc, _meta = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    assert enc["encoding"] == _ENCODING_TIMESTAMP_STRING_DELTA
    assert _decode_column(enc, len(values)) == values


def test_field_aware_deterministic_two_runs():
    values = [f"https://x.com/p/{i}/tail" for i in range(50)]
    e1, m1 = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    e2, m2 = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    assert e1 == e2
    assert m1["winner"] == m2["winner"]


def test_high_cardinality_random_no_regression_vs_v2():
    values = [f"{i:032x}" for i in range(200)]
    fa_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    v2_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _decode_column(fa_enc, len(values)) == values
    assert _msgpack_size(fa_enc) <= _msgpack_size(v2_enc)


def test_long_url_column_msgpack_beats_plain_v2():
    """Unique tails: v2 cannot use dictionary; field-aware prefix/suffix shrinks msgpack."""
    prefix = "https://cdn.example.com/" + "x/" * 25 + "assets/img/"
    values = [prefix + f"{i:04d}.png" for i in range(400)]
    fa_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    v2_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _msgpack_size(fa_enc) < _msgpack_size(v2_enc)
    assert _decode_column(fa_enc, len(values)) == values


def test_timestamp_column_msgpack_beats_plain_v2():
    values = [f"2024-03-10 10:{i // 60:02d}:{i % 60:02d}" for i in range(120)]
    fa_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2)
    v2_enc, _ = _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)
    assert _msgpack_size(fa_enc) < _msgpack_size(v2_enc)
    assert _decode_column(fa_enc, len(values)) == values


def test_url_corpus_field_aware_mck_not_larger_than_columnar_v2(tmp_path):
    body = b"".join(
        (
            b"INFO path=https://cdn.example.com/v1/assets/img/%03d.png "
            b"status=200\n" % (i % 17,)
        )
        for i in range(400)
    )
    files = {"urls.log": body}
    corpus = make_corpus(tmp_path, files)
    _, m_std = compress_corpus_template_with_metrics(corpus, adaptive="v2.2")
    _, m_fa = compress_corpus_template_with_metrics(corpus, adaptive="v2.2+field_aware")
    sz_std = m_std["candidate_sizes"]["columnar_encoding_v2"]
    sz_fa = m_fa["candidate_sizes"][_ADAPT_FIELD_AWARE]
    assert sz_fa <= sz_std


def test_timestamp_corpus_round_trip_with_field_aware_adaptive(tmp_path):
    lines = [f"INFO ts=2024-06-15 12:00:{i:02d} ok=1\n".encode() for i in range(400)]
    files = {"ts.log": b"".join(lines)}
    corpus = make_corpus(tmp_path, files)
    archive, _m = compress_corpus_template_with_metrics(
        corpus, adaptive="v2.2+field_aware"
    )
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    assert (out / "ts.log").read_bytes() == files["ts.log"]
