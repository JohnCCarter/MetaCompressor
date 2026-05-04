"""Tests for corpus (multi-file) compression and decompression."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metacompressor.corpus import compress_corpus, decompress_corpus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """Write *files* (relative-path → bytes) under *tmp_path* and return root."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        dest = corpus_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return corpus_dir


def round_trip_corpus(tmp_path: Path, files: dict[str, bytes]) -> dict[str, bytes]:
    """Compress then decompress *files*; return recovered {rel_path: bytes}."""
    corpus_dir = make_corpus(tmp_path, files)
    archive = compress_corpus(corpus_dir)
    out_dir = tmp_path / "recovered"
    decompress_corpus(archive, out_dir)
    return {rel.replace("\\", "/"): (out_dir / rel).read_bytes() for rel in files}


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestCorpusRoundTrip:
    def test_single_file(self, tmp_path):
        files = {"hello.txt": b"Hello, corpus mode!"}
        assert round_trip_corpus(tmp_path, files) == files

    def test_multiple_files(self, tmp_path):
        files = {
            "a.txt": b"file A content " * 100,
            "b.txt": b"file B content " * 100,
            "c.txt": b"file C content " * 100,
        }
        assert round_trip_corpus(tmp_path, files) == files

    def test_nested_directories(self, tmp_path):
        files = {
            "dir1/x.bin": os.urandom(512),
            "dir1/y.bin": os.urandom(512),
            "dir2/sub/z.bin": os.urandom(256),
            "root.txt": b"root level file",
        }
        assert round_trip_corpus(tmp_path, files) == files

    def test_empty_file(self, tmp_path):
        files = {"empty.bin": b"", "nonempty.txt": b"data"}
        assert round_trip_corpus(tmp_path, files) == files

    def test_binary_data(self, tmp_path):
        files = {f"file{i}.bin": bytes(range(256)) * 16 for i in range(5)}
        assert round_trip_corpus(tmp_path, files) == files

    def test_large_files(self, tmp_path):
        files = {
            "big1.dat": os.urandom(4096 * 10),
            "big2.dat": os.urandom(4096 * 10),
        }
        assert round_trip_corpus(tmp_path, files) == files


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------


class TestCorpusDeduplication:
    def test_identical_files_deduplicated(self, tmp_path):
        """Multiple files with the same content should compress tightly."""
        shared_content = b"shared chunk content " * 200
        files = {f"copy{i}.dat": shared_content for i in range(10)}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus(corpus_dir)
        total_raw = sum(len(d) for d in files.values())
        # Archive must be much smaller than the sum of uncompressed files
        assert len(archive) < total_raw // 4

    def test_cross_file_deduplication(self, tmp_path):
        """Shared chunks across files should only be stored once."""
        # Use random data for common and unique parts so ZSTD cannot trivially
        # compress them; this makes the cross-file deduplication benefit visible.
        common = os.urandom(4096 * 8)  # 8 chunks shared across all three files
        unique_a = os.urandom(4096 * 2)
        unique_b = os.urandom(4096 * 2)
        unique_c = os.urandom(4096 * 2)
        files = {
            "a.bin": common + unique_a,
            "b.bin": common + unique_b,
            "c.bin": common + unique_c,
        }
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus(corpus_dir)

        # Each file compressed independently with ZSTD (per-file baseline)
        import zstandard as zstd

        cctx = zstd.ZstdCompressor(level=3)
        zstd_total = sum(len(cctx.compress(d)) for d in files.values())

        # MC corpus stores the 8 common chunks once instead of three times.
        # Random data is incompressible so the saving is clearly visible.
        assert len(archive) < zstd_total

    def test_similar_log_files_beat_zstd(self, tmp_path):
        """Simulate a log corpus where MC should outperform per-file ZSTD."""
        import zstandard as zstd

        template = (
            "2024-01-01T00:{mm:02d}:{ss:02d}Z INFO request id={i} path=/api/data\n"
        )
        files: dict[str, bytes] = {}
        for day in range(10):
            lines = [
                template.format(mm=i // 60, ss=i % 60, i=i + day * 1000)
                for i in range(300)
            ]
            files[f"day{day:02d}.log"] = "".join(lines).encode()

        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus(corpus_dir)

        cctx = zstd.ZstdCompressor(level=3)
        zstd_total = sum(len(cctx.compress(d)) for d in files.values())

        assert len(archive) < zstd_total


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestCorpusDeterminism:
    def test_same_input_same_output(self, tmp_path):
        files = {"a.txt": b"hello " * 500, "b.txt": b"world " * 500}
        dir1 = make_corpus(tmp_path / "run1", files)
        dir2 = make_corpus(tmp_path / "run2", files)
        assert compress_corpus(dir1) == compress_corpus(dir2)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestCorpusErrors:
    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_bytes(b"data")
        with pytest.raises(ValueError, match="Not a directory"):
            compress_corpus(f)

    def test_corrupt_magic(self, tmp_path):
        files = {"x.txt": b"data"}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus(corpus_dir)
        bad = b"XXXX" + archive[4:]
        with pytest.raises(ValueError):
            decompress_corpus(bad, tmp_path / "out")

    def test_truncated_archive(self, tmp_path):
        with pytest.raises(ValueError):
            decompress_corpus(b"\x00\x01\x02", tmp_path / "out")

    def test_output_dir_created(self, tmp_path):
        files = {"f.txt": b"content"}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus(corpus_dir)
        out = tmp_path / "new" / "deep" / "dir"
        decompress_corpus(archive, out)
        assert (out / "f.txt").read_bytes() == b"content"
