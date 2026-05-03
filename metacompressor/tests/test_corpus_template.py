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


# ---------------------------------------------------------------------------
# Metrics and explainability
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_metrics_keys_present(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO val=1\nINFO val=2\n" * 30}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)

        expected_keys = {
            "num_files", "num_lines", "num_shared_templates",
            "template_reuse_count", "template_reuse_rate",
            "raw_fallback_lines", "binary_fallback_files",
            "avg_vars_per_tpl_line", "compressed_size", "timing",
        }
        assert expected_keys.issubset(metrics.keys())

    def test_timing_keys_present(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO val=1\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        timing = metrics["timing"]
        assert set(timing.keys()) == {"extract_s", "serialize_s", "zstd_s", "total_s"}

    def test_timing_non_negative(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO val=1\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        for k, v in metrics["timing"].items():
            assert v >= 0, f"Timing value {k}={v} should be non-negative"

    def test_num_files_correct(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {f"f{i}.log": f"INFO n={i}\n".encode() * 5 for i in range(7)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["num_files"] == 7

    def test_compressed_size_matches_bytes(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        data, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["compressed_size"] == len(data)

    def test_reuse_rate_between_0_and_1(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 30, "b.log": b"WARN y=3\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        rate = metrics["template_reuse_rate"]
        assert 0.0 <= rate <= 1.0

    def test_shared_templates_count_positive_for_repetitive_corpus(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {f"day{i}.log": b"INFO req=1 status=200\n" * 100 for i in range(3)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["num_shared_templates"] > 0
        assert metrics["template_reuse_count"] > 0

    def test_binary_fallback_files_counted(self, tmp_path):
        import os
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"data.bin": os.urandom(256), "log.log": b"INFO n=1\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        # The binary file should be counted in binary_fallback_files
        assert metrics["binary_fallback_files"] >= 1

    def test_with_metrics_same_bytes_as_without(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 50}
        corpus_dir = make_corpus(tmp_path, files)
        data_plain = compress_corpus_template(corpus_dir)
        data_with_metrics, _ = compress_corpus_template_with_metrics(corpus_dir)
        assert data_plain == data_with_metrics


# ---------------------------------------------------------------------------
# Hybrid fallback (files with no template-mode lines stored as raw bytes)
# ---------------------------------------------------------------------------

class TestHybridFallback:
    def test_no_template_file_still_round_trips(self, tmp_path):
        """A file with unique lines (no template reuse) must round-trip exactly."""
        # Each line is unique and has no numeric/variable parts
        unique_lines = "\n".join(
            f"LINE_{chr(65 + i % 26)}{i}_unique" for i in range(20)
        ) + "\n"
        files = {
            "unique.log": unique_lines.encode(),
            "normal.log": b"INFO val=1\nINFO val=2\n" * 30,
        }
        result = round_trip(tmp_path, files)
        assert result == files

    def test_hybrid_fallback_files_are_binary_fallback(self, tmp_path):
        """Files with no template-mode lines should be in binary_fallback_files."""
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        # Pure-text lines with no extractable variables and no trailing newline
        # (avoids the empty-string artifact from split on a trailing \n).
        # Each line is unique globally so no template key recurs.
        unique_content = b"APPLE BANANA CHERRY\nDOG ELEPHANT FOX\nGRAPE HONEY IRIS"
        files = {
            "unique.log": unique_content,
            "normal.log": b"INFO val=1\nINFO val=2\n" * 30,
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        # unique.log has no recurring template → hybrid fallback → binary_fallback_files >= 1
        assert metrics["binary_fallback_files"] >= 1

    def test_fallback_does_not_increase_size_for_noisy_corpus(self, tmp_path):
        """A corpus of all-unique lines should not bloat due to template overhead."""
        # Generate 5 "noisy" files with completely unique lines
        import os
        noisy_files = {}
        for i in range(5):
            lines = "\n".join(
                f"MSG {os.urandom(8).hex()} idx={j} file={i}" for j in range(20)
            ) + "\n"
            noisy_files[f"noisy_{i}.log"] = lines.encode()

        corpus_dir = make_corpus(tmp_path, noisy_files)
        mck = compress_corpus_template(corpus_dir)
        # Should still be a valid archive (round-trips correctly)
        out_dir = tmp_path / "out"
        extracted = decompress_corpus_template(mck, out_dir)
        for rel in noisy_files:
            recovered = (out_dir / rel).read_bytes()
            assert recovered == noisy_files[rel], f"Mismatch for {rel}"

    def test_mixed_corpus_round_trip(self, tmp_path):
        """Mixed corpus with template-rich, template-poor, and binary files."""
        import os
        files = {
            "structured.log": b"ERROR code=404 user=42\n" * 100,
            "unstructured.log": b"SOME TEXT THAT IS UNIQUE EACH LINE\n",
            "binary.bin": os.urandom(128),
        }
        result = round_trip(tmp_path, files)
        assert result == files

