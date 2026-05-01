"""Tests for corpus-mode (multi-file, shared-dictionary) compression."""

from __future__ import annotations

import os

import pytest

from metacompressor.compressor import compress, compress_corpus, CHUNKING_FIXED, CHUNKING_CDC
from metacompressor.decompressor import decompress, decompress_corpus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def corpus_round_trip(
    files: list[tuple[str, bytes]], **kwargs
) -> list[tuple[str, bytes]]:
    return decompress_corpus(compress_corpus(files, **kwargs))


# ---------------------------------------------------------------------------
# Basic round-trip tests
# ---------------------------------------------------------------------------

class TestCorpusRoundTrip:
    def test_single_file(self):
        files = [("a.txt", b"Hello, corpus world!")]
        assert corpus_round_trip(files) == files

    def test_empty_corpus(self):
        assert corpus_round_trip([]) == []

    def test_empty_file_in_corpus(self):
        files = [("empty.bin", b""), ("nonempty.txt", b"data")]
        result = corpus_round_trip(files)
        assert dict(result) == dict(files)

    def test_multiple_files(self):
        files = [
            ("a/b.txt", b"file a/b content " * 50),
            ("c.bin", os.urandom(8192)),
            ("d.log", b"log line\n" * 200),
        ]
        result = corpus_round_trip(files)
        assert dict(result) == dict(files)

    def test_all_files_byte_for_byte(self):
        files = [
            (f"file_{i}.dat", os.urandom(4096 + i * 100))
            for i in range(10)
        ]
        result = corpus_round_trip(files)
        result_map = dict(result)
        for path, data in files:
            assert result_map[path] == data

    def test_repeated_content_across_files(self):
        """Identical content in multiple files round-trips correctly."""
        shared = b"shared block " * 500
        files = [
            ("file1.txt", shared),
            ("file2.txt", shared),
            ("file3.txt", shared + b"extra"),
        ]
        result = corpus_round_trip(files)
        assert dict(result) == dict(files)

    def test_large_binary_files(self):
        files = [
            ("big1.bin", bytes(range(256)) * 200),
            ("big2.bin", bytes(range(255, -1, -1)) * 200),
        ]
        result = corpus_round_trip(files)
        assert dict(result) == dict(files)

    def test_nested_paths(self):
        files = [
            ("a/b/c/deep.txt", b"deep content"),
            ("a/b/shallow.txt", b"shallow"),
            ("root.txt", b"root"),
        ]
        result = corpus_round_trip(files)
        assert dict(result) == dict(files)


# ---------------------------------------------------------------------------
# CDC mode round-trip
# ---------------------------------------------------------------------------

class TestCorpusRoundTripCDC:
    def test_multiple_files_cdc(self):
        files = [
            ("alpha.log", (b"INFO server started\n" * 300)),
            ("beta.log", (b"DEBUG request handled\n" * 300)),
        ]
        result = corpus_round_trip(files, chunking_mode=CHUNKING_CDC)
        assert dict(result) == dict(files)

    def test_repeated_content_cdc(self):
        shared = b"X" * 4096
        files = [(f"f{i}.bin", shared) for i in range(5)]
        result = corpus_round_trip(files, chunking_mode=CHUNKING_CDC)
        assert dict(result) == dict(files)


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------

class TestCorpusDeterminism:
    def test_same_input_same_output(self):
        files = [
            ("a.txt", b"determinism " * 200),
            ("b.bin", b"\x00\x01\x02" * 300),
        ]
        c1 = compress_corpus(files)
        c2 = compress_corpus(files)
        assert c1 == c2

    def test_order_independent(self):
        """Passing files in different order must produce the same output."""
        files = [
            ("a.txt", b"content a " * 100),
            ("b.txt", b"content b " * 100),
            ("c.txt", b"content c " * 100),
        ]
        reversed_files = list(reversed(files))
        c1 = compress_corpus(files)
        c2 = compress_corpus(reversed_files)
        assert c1 == c2

    def test_different_contents_differ(self):
        files_a = [("f.txt", b"aaa" * 1000)]
        files_b = [("f.txt", b"bbb" * 1000)]
        assert compress_corpus(files_a) != compress_corpus(files_b)

    def test_different_paths_differ(self):
        files_a = [("x.txt", b"same content")]
        files_b = [("y.txt", b"same content")]
        assert compress_corpus(files_a) != compress_corpus(files_b)


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestCorpusDeduplication:
    def test_cross_file_deduplication(self):
        """Repeated chunk across multiple files produces a smaller corpus."""
        chunk = b"Z" * 4096
        # 10 files each with the same 50-chunk block
        files = [(f"f{i}.bin", chunk * 50) for i in range(10)]
        mc1 = compress_corpus(files)
        total_raw = sum(len(d) for _, d in files)
        # Should be dramatically smaller due to cross-file dedup
        assert len(mc1) < total_raw // 5

    def test_within_file_deduplication_still_works(self):
        """Single-file deduplication in corpus mode."""
        chunk = b"Y" * 4096
        files = [("single.bin", chunk * 100)]
        mc1 = compress_corpus(files)
        total_raw = sum(len(d) for _, d in files)
        assert len(mc1) < total_raw // 2


# ---------------------------------------------------------------------------
# Container metadata tests
# ---------------------------------------------------------------------------

class TestCorpusMetadata:
    def test_fixed_mode_stored(self):
        from metacompressor.container import deserialise_corpus
        files = [("a.txt", b"x" * 5000)]
        mc1 = compress_corpus(files, chunking_mode=CHUNKING_FIXED)
        c = deserialise_corpus(mc1)
        assert c.chunking_mode == "fixed"
        assert c.chunk_size == 4096

    def test_cdc_mode_stored(self):
        from metacompressor.container import deserialise_corpus
        files = [("a.txt", b"x" * 10000)]
        mc1 = compress_corpus(files, chunking_mode=CHUNKING_CDC)
        c = deserialise_corpus(mc1)
        assert c.chunking_mode == "cdc"
        assert c.min_chunk_size is not None
        assert c.avg_chunk_size is not None
        assert c.max_chunk_size is not None
        assert c.cdc_mask is not None

    def test_file_paths_preserved(self):
        from metacompressor.container import deserialise_corpus
        files = [("a/b.txt", b"hello"), ("c.bin", b"world")]
        mc1 = compress_corpus(files)
        c = deserialise_corpus(mc1)
        stored_paths = [f.path for f in c.files]
        assert stored_paths == ["a/b.txt", "c.bin"]


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestCorpusErrorHandling:
    def test_decompress_corpus_on_single_file_raises(self):
        """A single-file .mc1 must not be accepted by decompress_corpus."""
        mc1 = compress(b"test data")
        with pytest.raises(ValueError, match="single-file"):
            decompress_corpus(mc1)

    def test_decompress_on_corpus_file_raises(self):
        """A corpus .mc1 must not be accepted by single-file decompress."""
        mc1 = compress_corpus([("f.txt", b"data")])
        with pytest.raises(ValueError, match="corpus"):
            decompress(mc1)

    def test_corrupt_magic(self):
        mc1 = compress_corpus([("f.txt", b"data")])
        corrupted = b"BAD!" + mc1[4:]
        with pytest.raises(ValueError, match="magic"):
            decompress_corpus(corrupted)

    def test_truncated_data(self):
        with pytest.raises(ValueError):
            decompress_corpus(b"\x00\x01\x02")

    def test_invalid_chunking_mode(self):
        with pytest.raises(ValueError, match="Unknown chunking_mode"):
            compress_corpus([("f.txt", b"data")], chunking_mode="invalid")


# ---------------------------------------------------------------------------
# Backward-compatibility: existing single-file .mc1 tests unaffected
# ---------------------------------------------------------------------------

class TestCorpusBackwardCompat:
    def test_single_file_compress_still_works(self):
        data = b"backward compat check " * 200
        assert decompress(compress(data)) == data

    def test_corpus_version_different_from_single_file(self):
        from metacompressor.container import VERSION, CORPUS_VERSION
        assert CORPUS_VERSION != VERSION

    def test_corpus_and_single_file_different_bytes(self):
        data = b"same bytes"
        single = compress(data)
        corpus = compress_corpus([("f.txt", data)])
        assert single != corpus
