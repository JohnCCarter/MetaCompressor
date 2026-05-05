"""Adaptive mode selection v1 (row vs columnar v1/v2 vs TAR+ZSTD in MCK)."""

from __future__ import annotations

import msgpack
import zstandard as zstd

import metacompressor.corpus_template as ct
from metacompressor.corpus_template import (
    _ADAPT_COL_V2,
    _ADAPT_ROW,
    _ADAPT_TAR,
    _MODE_COLUMNAR_V2,
    _MODE_RAW_TAR_ZSTD,
    _MODE_ROW_V1,
    MAGIC,
    VERSION,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.tests.test_corpus_template import make_corpus


def _empty_fb_stats() -> dict:
    return {"fallback_reason_counts": {}}


def test_row_selected_when_smaller(monkeypatch):
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", float("inf"))
    monkeypatch.setattr(ct, "_build_raw_tarzstd_archive", lambda _tb: b"T" * 40)

    data, mode, raw, meta, _fb = ct._adaptive_select_output(
        tarzstd_bytes=b"z",
        tarzstd_size=100,
        row_result=b"R" * 10,
        row_stats=_empty_fb_stats(),
        columnar_v2_result=b"2" * 30,
        columnar_v2_stats=_empty_fb_stats(),
        columnar_v1_result=b"1" * 25,
        columnar_v1_stats=_empty_fb_stats(),
    )
    assert not raw
    assert mode == _MODE_ROW_V1
    assert meta["selected_mode"] == _ADAPT_ROW
    assert len(data) == 10


def test_columnar_selected_when_smaller(monkeypatch):
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", float("inf"))
    monkeypatch.setattr(ct, "_build_raw_tarzstd_archive", lambda _tb: b"T" * 50)

    data, mode, raw, meta, _fb = ct._adaptive_select_output(
        tarzstd_bytes=b"z",
        tarzstd_size=100,
        row_result=b"R" * 100,
        row_stats=_empty_fb_stats(),
        columnar_v2_result=b"2" * 20,
        columnar_v2_stats=_empty_fb_stats(),
        columnar_v1_result=b"1" * 30,
        columnar_v1_stats=_empty_fb_stats(),
    )
    assert not raw
    assert mode == _MODE_COLUMNAR_V2
    assert meta["selected_mode"] == _ADAPT_COL_V2
    assert len(data) == 20


def test_tar_zstd_selected_when_smaller(monkeypatch):
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", 1.0)
    monkeypatch.setattr(ct, "_build_raw_tarzstd_archive", lambda _tb: b"Z" * 12)

    data, mode, raw, meta, _fb = ct._adaptive_select_output(
        tarzstd_bytes=b"z",
        tarzstd_size=10,
        row_result=b"R" * 50,
        row_stats=_empty_fb_stats(),
        columnar_v2_result=b"2" * 50,
        columnar_v2_stats=_empty_fb_stats(),
        columnar_v1_result=b"1" * 50,
        columnar_v1_stats=_empty_fb_stats(),
    )
    assert raw
    assert mode == _MODE_RAW_TAR_ZSTD
    assert meta["selected_mode"] == _ADAPT_TAR
    assert len(data) == 12


def test_deterministic_selection_tiebreak_prefers_row(monkeypatch):
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", float("inf"))
    monkeypatch.setattr(ct, "_build_raw_tarzstd_archive", lambda _tb: b"T" * 99)

    same = b"X" * 20
    data, _mode, raw, meta, _fb = ct._adaptive_select_output(
        tarzstd_bytes=b"z",
        tarzstd_size=100,
        row_result=same,
        row_stats=_empty_fb_stats(),
        columnar_v2_result=same,
        columnar_v2_stats=_empty_fb_stats(),
        columnar_v1_result=same,
        columnar_v1_stats=_empty_fb_stats(),
    )
    assert not raw
    assert meta["selected_mode"] == _ADAPT_ROW
    assert data == same


def test_deterministic_selection_meta(monkeypatch):
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", float("inf"))
    monkeypatch.setattr(ct, "_build_raw_tarzstd_archive", lambda _tb: b"T" * 40)

    args = dict(
        tarzstd_bytes=b"z",
        tarzstd_size=100,
        row_result=b"R" * 15,
        row_stats=_empty_fb_stats(),
        columnar_v2_result=b"2" * 25,
        columnar_v2_stats=_empty_fb_stats(),
        columnar_v1_result=b"1" * 20,
        columnar_v1_stats=_empty_fb_stats(),
    )
    _a1, _b1, _c1, meta1, _ = ct._adaptive_select_output(**args)
    _a2, _b2, _c2, meta2, _ = ct._adaptive_select_output(**args)
    assert meta1 == meta2


def test_byte_perfect_decompression_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", float("inf"))
    files = {
        "a.log": b"".join(
            f"INFO seq={i} status={i % 5} user={i % 9} code={200 + (i % 3)}\n".encode()
            for i in range(200)
        ),
    }
    corpus_dir = make_corpus(tmp_path, files)
    archive, metrics = compress_corpus_template_with_metrics(corpus_dir)
    out = tmp_path / "out"
    decompress_corpus_template(archive, out)
    assert (out / "a.log").read_bytes() == files["a.log"]
    assert (
        metrics["compressed_size"]
        == metrics["candidate_sizes"][metrics["selected_mode"]]
    )


def test_compress_tar_fallback_when_template_gate_excludes_all(monkeypatch, tmp_path):
    """With threshold 0, no row/columnar candidate is eligible; TAR+MCK wins."""
    monkeypatch.setattr(ct, "_CORPUS_FALLBACK_THRESHOLD", 0.0)
    files = {"a.log": b"INFO x=1\nINFO x=2\n" * 40}
    corpus_dir = make_corpus(tmp_path, files)
    _, metrics = compress_corpus_template_with_metrics(corpus_dir)
    assert metrics["selected_mode"] == _ADAPT_TAR
    assert metrics["final_selected_mode"] == _MODE_RAW_TAR_ZSTD
    assert metrics["chose_raw_fallback"] is True


def test_old_archives_still_decompress_row_template(tmp_path):
    payload = {
        "templates": ["INFO seq={} status={}"],
        "files": [
            {
                "path": "legacy.log",
                "records": [[0, ["1", "200"]], [0, ["2", "404"]]],
            }
        ],
    }
    archive = (
        MAGIC
        + bytes([VERSION])
        + zstd.ZstdCompressor(level=3).compress(
            msgpack.packb(payload, use_bin_type=True)
        )
    )
    out_dir = tmp_path / "legacy_out"
    decompress_corpus_template(archive, out_dir)
    assert (
        out_dir / "legacy.log"
    ).read_bytes() == b"INFO seq=1 status=200\nINFO seq=2 status=404"
