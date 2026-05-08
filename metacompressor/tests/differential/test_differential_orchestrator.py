from __future__ import annotations

import json
from pathlib import Path

from metacompressor.corpus import decompress_corpus
from metacompressor.differential import (
    ARCHIVE_FILENAME,
    MANIFEST_FILENAME,
    RECEIPTS_FILENAME,
    compress_corpus_differential,
)
from metacompressor.differential.orchestrator import _CACHE_META_FILENAME


def _write_corpus(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


_SAMPLE = {
    "alpha.txt": b"AAAA" * 128,
    "beta.txt": b"BBBB" * 128,
    "sub/gamma.txt": b"CCCC" * 64,
}


def test_first_run_creates_cache_files(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    assert (cache / MANIFEST_FILENAME).exists()
    assert (cache / RECEIPTS_FILENAME).exists()
    assert (cache / ARCHIVE_FILENAME).exists()
    assert (cache / _CACHE_META_FILENAME).exists()


def test_second_run_identical_input_is_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is True


def test_second_run_returns_fresh_archive(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    result = compress_corpus_differential(corpus, cache)
    # Phase 2: always returns fresh archive — it must be valid and non-empty
    assert isinstance(result.archive, bytes)
    assert len(result.archive) > 0


def test_report_contains_expected_keys(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    result = compress_corpus_differential(corpus, cache)
    expected_keys = {
        "cache_hit_candidate",
        "archives_equal",
        "fail_closed",
        "reason",
        "reuse_chunk_count",
        "rescan_chunk_count",
        "miss_reasons",
        "reusable_but_not_hit_chunks",
        "partial_reuse_opportunity",
    }
    assert expected_keys == set(result.report.keys())


def test_output_decompresses_losslessly(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    out = tmp_path / "out"
    _write_corpus(corpus, _SAMPLE)
    result = compress_corpus_differential(corpus, cache)
    decompress_corpus(result.archive, out)
    for rel, expected in _SAMPLE.items():
        assert (out / rel).read_bytes() == expected


def test_changed_file_not_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    # Mutate one file
    (corpus / "alpha.txt").write_bytes(b"XXXX" * 128)
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is False


def test_corrupt_manifest_full_recompression_no_exception(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    (cache / MANIFEST_FILENAME).write_text("not json {{{")
    result = compress_corpus_differential(corpus, cache)
    assert isinstance(result.archive, bytes)
    assert result.report["cache_hit_candidate"] is False


def test_missing_archive_not_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    (cache / ARCHIVE_FILENAME).unlink()
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is False


def test_chunk_size_mismatch_not_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache, chunk_size=512)
    result = compress_corpus_differential(corpus, cache, chunk_size=1024)
    assert result.report["cache_hit_candidate"] is False


def test_use_delta_mismatch_not_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache, use_delta=False)
    result = compress_corpus_differential(corpus, cache, use_delta=True)
    assert result.report["cache_hit_candidate"] is False


def test_compressor_version_mismatch_not_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    meta_path = cache / _CACHE_META_FILENAME
    meta = json.loads(meta_path.read_text())
    meta["compressor_version"] = "99.99.99"
    meta_path.write_text(json.dumps(meta))
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is False


def test_determinism_two_runs_equal_output(tmp_path: Path) -> None:
    c1, cache1 = tmp_path / "c1", tmp_path / "cache1"
    c2, cache2 = tmp_path / "c2", tmp_path / "cache2"
    _write_corpus(c1, _SAMPLE)
    _write_corpus(c2, _SAMPLE)
    r1 = compress_corpus_differential(c1, cache1)
    r2 = compress_corpus_differential(c2, cache2)
    assert r1.archive == r2.archive


def test_archives_equal_when_hit_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is True
    assert result.report["archives_equal"] is True


def test_archives_equal_none_when_not_candidate(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is False
    assert result.report["archives_equal"] is None


def test_report_fail_closed_false_on_first_run(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    result = compress_corpus_differential(corpus, cache)
    assert result.report["fail_closed"] is False


def test_added_file_triggers_rescan(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    cache = tmp_path / "cache"
    _write_corpus(corpus, _SAMPLE)
    compress_corpus_differential(corpus, cache)
    (corpus / "new.txt").write_bytes(b"new data here")
    result = compress_corpus_differential(corpus, cache)
    assert result.report["cache_hit_candidate"] is False
    assert result.report["rescan_chunk_count"] > 0
