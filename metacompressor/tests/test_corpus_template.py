"""Tests for corpus template mode (shared template dictionary)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metacompressor.corpus_template import (
    compress_corpus_template,
    decompress_corpus_template,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_corpus(tmp_path: Path, files: dict) -> Path:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        dest = corpus_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return corpus_dir


def round_trip(tmp_path: Path, files: dict) -> dict:
    corpus_dir = make_corpus(tmp_path, files)
    archive = compress_corpus_template(corpus_dir)
    out_dir = tmp_path / "recovered"
    decompress_corpus_template(archive, out_dir)
    return {
        rel.replace("\\", "/"): (out_dir / rel).read_bytes()
        for rel in files
    }


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class TestCorpusTemplateRoundTrip:
    def test_single_text_file(self, tmp_path):
        lines = [f"INFO event={i}\n" for i in range(50)]
        files = {"log.txt": "".join(lines).encode()}
        assert round_trip(tmp_path, files) == files

    def test_multiple_text_files(self, tmp_path):
        template = "ERROR user={i} status=500\n"
        files = {
            f"day{d}.log": "".join(
                template.format(i=i + d * 1000) for i in range(100)
            ).encode()
            for d in range(5)
        }
        assert round_trip(tmp_path, files) == files

    def test_nested_directories(self, tmp_path):
        files = {
            "a/b.log": b"INFO val=1\nINFO val=2\nINFO val=3\n" * 10,
            "a/c.log": b"WARN val=4\nWARN val=5\n" * 10,
            "d.log": b"ERROR code=42\n" * 20,
        }
        assert round_trip(tmp_path, files) == files

    def test_empty_file(self, tmp_path):
        files = {"empty.txt": b"", "nonempty.txt": b"INFO x=1\nINFO x=2\n" * 5}
        assert round_trip(tmp_path, files) == files

    def test_binary_file_preserved(self, tmp_path):
        binary_data = os.urandom(512)
        files = {"data.bin": binary_data, "log.txt": b"INFO n=1\nINFO n=2\n" * 5}
        assert round_trip(tmp_path, files) == files

    def test_no_trailing_newline(self, tmp_path):
        # Last line has no newline – must round-trip exactly
        data = b"INFO x=1\nINFO x=2\nINFO x=3"
        files = {"no_nl.log": data}
        assert round_trip(tmp_path, files) == files

    def test_non_utf8_binary_preserved(self, tmp_path):
        files = {"img.bin": bytes(range(256)) * 4}
        assert round_trip(tmp_path, files) == files


# ---------------------------------------------------------------------------
# Shared dictionary / compression quality tests
# ---------------------------------------------------------------------------

class TestSharedDictionary:
    def test_shared_templates_across_files(self, tmp_path):
        """Templates recurring across files should compress better than per-file."""
        import zstandard as zstd

        template = "2024-01-01T00:{mm:02d}:{ss:02d}Z INFO req={i} path=/api/v1\n"
        files = {}
        for day in range(10):
            lines = [
                template.format(mm=i // 60, ss=i % 60, i=i + day * 1000)
                for i in range(200)
            ]
            files[f"day{day:02d}.log"] = "".join(lines).encode()

        corpus_dir = make_corpus(tmp_path, files)
        mck = compress_corpus_template(corpus_dir)

        cctx = zstd.ZstdCompressor(level=3)
        zstd_total = sum(len(cctx.compress(d)) for d in files.values())

        # Corpus template should match or beat per-file ZSTD on a uniform log corpus
        assert len(mck) <= zstd_total

    def test_archive_smaller_with_shared_templates(self, tmp_path):
        """A corpus with one dominant recurring template should deduplicate well."""
        line = "METRIC host=server-{n} cpu={c} mem={m}\n"
        files = {
            f"metric{i}.log": "".join(
                line.format(n=i, c=j % 100, m=j * 2 % 1000) for j in range(300)
            ).encode()
            for i in range(8)
        }
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        total_raw = sum(len(d) for d in files.values())
        # Must be much smaller than raw
        assert len(archive) < total_raw // 3


# ---------------------------------------------------------------------------
# Format / error tests
# ---------------------------------------------------------------------------

class TestCorpusTemplateFormat:
    def test_magic_bytes(self, tmp_path):
        files = {"a.txt": b"INFO x=1\nINFO x=2\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        assert archive[:4] == b"MCK\x00"

    def test_version_byte(self, tmp_path):
        files = {"a.txt": b"INFO x=1\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        assert archive[4] == 0x01

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_bytes(b"data")
        with pytest.raises(ValueError, match="Not a directory"):
            compress_corpus_template(f)

    def test_corrupt_magic(self, tmp_path):
        files = {"a.txt": b"INFO x=1\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        bad = b"XXXX" + archive[4:]
        with pytest.raises(ValueError):
            decompress_corpus_template(bad, tmp_path / "out")

    def test_truncated_archive(self, tmp_path):
        with pytest.raises(ValueError):
            decompress_corpus_template(b"\x00\x01\x02", tmp_path / "out")

    def test_output_dir_created(self, tmp_path):
        files = {"f.txt": b"INFO n=1\nINFO n=2\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        out = tmp_path / "new" / "deep" / "dir"
        result = decompress_corpus_template(archive, out)
        assert (out / "f.txt").read_bytes() == files["f.txt"]
        assert result == ["f.txt"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self, tmp_path):
        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 100, "b.log": b"WARN y=3\n" * 50}
        dir1 = make_corpus(tmp_path / "run1", files)
        dir2 = make_corpus(tmp_path / "run2", files)
        assert compress_corpus_template(dir1) == compress_corpus_template(dir2)
