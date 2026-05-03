"""Tests for CLI utilities, primarily the format_delta reporting function."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from metacompressor.cli import cmd_compare_dir, format_delta


class TestFormatDelta:
    """format_delta(mc_size, baseline_size, baseline_label) -> str"""

    def test_mc_smaller_reports_smaller(self):
        result = format_delta(167_689, 188_587, "TAR+ZSTD")
        assert "SMALLER" in result
        assert "LARGER" not in result
        assert "20,898" in result

    def test_mc_smaller_percentage_correct(self):
        # 20898 / 188587 ≈ 11.1%
        result = format_delta(167_689, 188_587, "TAR+ZSTD")
        assert "11.1%" in result

    def test_mc_larger_reports_larger(self):
        result = format_delta(200_000, 188_587, "TAR+ZSTD")
        assert "LARGER" in result
        assert "SMALLER" not in result
        assert "11,413" in result

    def test_mc_larger_percentage_correct(self):
        # 11413 / 188587 ≈ 6.1%
        result = format_delta(200_000, 188_587, "TAR+ZSTD")
        assert "6.1%" in result

    def test_mc_equal_size(self):
        result = format_delta(100_000, 100_000, "TAR+ZSTD")
        assert "equal" in result.lower()
        assert "SMALLER" not in result
        assert "LARGER" not in result

    def test_baseline_label_in_output(self):
        result = format_delta(50_000, 60_000, "ZSTD per-file")
        assert "ZSTD per-file" in result

    def test_zero_baseline_handled(self):
        # Should not raise; returns a sensible message.
        result = format_delta(100, 0, "TAR+ZSTD")
        assert isinstance(result, str)

    def test_mc_smaller_by_one_byte(self):
        result = format_delta(999, 1000, "baseline")
        assert "SMALLER" in result
        assert "1" in result

    def test_mc_larger_by_one_byte(self):
        result = format_delta(1001, 1000, "baseline")
        assert "LARGER" in result
        assert "1" in result


class TestCompareDirOutput:
    def test_compare_dir_includes_columnar_metrics(self, tmp_path, capsys):
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        (corpus_dir / "logs.log").write_bytes(
            b"INFO seq=1 status=200\nINFO seq=2 status=200\n" * 80
        )

        cmd_compare_dir(Namespace(input_dir=str(corpus_dir)))

        output = capsys.readouterr().out
        assert "MC template columnar" in output
        assert "Columnar enabled" in output
        assert "Columnar templates" in output
        assert "Encoded columns" in output
        assert "Final selected mode" in output
