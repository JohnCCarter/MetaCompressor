"""Tests for log template extraction (compress_log / decompress_log)."""

from __future__ import annotations

import os

import pytest

from metacompressor.log_template import (
    TEMPLATE_MODE_VALIDATE,
    compress_log,
    decompress_log,
    get_compress_mode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def round_trip(data: bytes) -> bytes:
    return decompress_log(compress_log(data))


# ---------------------------------------------------------------------------
# Round-trip (lossless) tests
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_empty(self):
        assert round_trip(b"") == b""

    def test_single_line_no_numbers(self):
        data = b"INFO server started\n"
        assert round_trip(data) == data

    def test_single_unique_line_no_newline(self):
        data = b"DEBUG nothing interesting here"
        assert round_trip(data) == data

    def test_repeated_identical_lines(self):
        data = (b"ERROR user=123 latency=45ms\n" * 50)
        assert round_trip(data) == data

    def test_two_templates(self):
        lines = []
        for i in range(100):
            lines.append(f"ERROR user={i} latency={i % 50}ms\n")
            lines.append(f"INFO  req={i} path=/api status=200\n")
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_mixed_template_and_raw_lines(self):
        # Recurring template lines + unique lines
        lines = [f"ERROR user={i} latency={i}ms\n" for i in range(20)]
        lines += ["WARN unique one-off message\n", "WARN another unique\n"]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_no_trailing_newline(self):
        data = b"ERROR user=1 latency=10ms\nERROR user=2 latency=20ms"
        assert round_trip(data) == data

    def test_trailing_newline_preserved(self):
        data = b"ERROR user=1 latency=10ms\nERROR user=2 latency=20ms\n"
        assert round_trip(data) == data

    def test_floats_in_template(self):
        lines = [f"METRIC cpu={99.5 - i * 0.1:.1f} mem={i * 1.5:.1f}\n" for i in range(30)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_ip_addresses(self):
        lines = [f"CONN src=192.168.1.{i} dst=10.0.0.1 port={1024+i}\n" for i in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_timestamps(self):
        lines = [
            f"2024-01-01T00:{i // 60:02d}:{i % 60:02d}Z INFO user={i} ok\n"
            for i in range(200)
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_binary_data_fallback(self):
        # Non-UTF-8 binary → raw mode, still round-trips correctly
        data = bytes(range(256)) * 10
        assert round_trip(data) == data

    def test_large_log_file(self):
        lines = [
            f"ERROR user={i} latency={i % 500}ms status={400 + i % 200}\n"
            for i in range(5000)
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_all_unique_lines_no_numbers(self):
        # Each line is different and has no numbers → raw mode, still correct
        lines = [f"LINE_{chr(65 + i % 26)}{i} some text\n" for i in range(5)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_negative_numbers(self):
        lines = [f"METRIC temperature=-{i} delta={i - 50}\n" for i in range(30)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_key_equals_value_pattern(self):
        """Exact pattern from the problem statement example."""
        lines = [
            "ERROR user=123 latency=45ms\n",
            "ERROR user=456 latency=30ms\n",
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data


# ---------------------------------------------------------------------------
# Template mode selection
# ---------------------------------------------------------------------------

class TestTemplateMode:
    def test_recurring_lines_use_template_mode(self):
        lines = [f"ERROR user={i} latency={i}ms\n" for i in range(10)]
        data = "".join(lines).encode()
        compressed = compress_log(data)
        assert get_compress_mode(compressed) == TEMPLATE_MODE_VALIDATE

    def test_template_mode_validate_constant(self):
        """TEMPLATE_MODE_VALIDATE must equal the documented string."""
        assert TEMPLATE_MODE_VALIDATE == "TEMPLATE_MODE_VALIDATE"

    def test_raw_mode_for_all_unique_lines(self):
        # Every line is unique (no numbers, all distinct text) → raw mode
        lines = ["ALPHA\n", "BETA\n", "GAMMA\n", "DELTA\n", "EPSILON\n"]
        data = "".join(lines).encode()
        compressed = compress_log(data)
        assert get_compress_mode(compressed) == "raw"

    def test_raw_mode_for_empty_input(self):
        compressed = compress_log(b"")
        assert get_compress_mode(compressed) == "raw"

    def test_raw_mode_for_binary(self):
        data = bytes(range(256)) * 4
        compressed = compress_log(data)
        assert get_compress_mode(compressed) == "raw"

    def test_two_identical_lines_trigger_template_mode(self):
        data = b"ERROR user=1 latency=10ms\nERROR user=2 latency=20ms\n"
        compressed = compress_log(data)
        assert get_compress_mode(compressed) == TEMPLATE_MODE_VALIDATE

    def test_problem_statement_example(self):
        """The exact example from the problem statement must use template mode."""
        data = (
            b"ERROR user=123 latency=45ms\n"
            b"ERROR user=456 latency=30ms\n"
        )
        compressed = compress_log(data)
        assert get_compress_mode(compressed) == TEMPLATE_MODE_VALIDATE
        assert round_trip(data) == data


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        lines = [f"ERROR user={i} latency={i % 100}ms\n" for i in range(200)]
        data = "".join(lines).encode()
        assert compress_log(data) == compress_log(data)

    def test_different_inputs_differ(self):
        a = compress_log(b"ERROR user=1 latency=1ms\n" * 10)
        b = compress_log(b"INFO  req=1 status=200\n" * 10)
        assert a != b


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_corrupt_magic(self):
        compressed = compress_log(b"ERROR user=1 latency=1ms\n" * 5)
        bad = b"XXXX" + compressed[4:]
        with pytest.raises(ValueError, match="magic"):
            decompress_log(bad)

    def test_truncated_data(self):
        with pytest.raises(ValueError):
            decompress_log(b"\x00\x01\x02")

    def test_corrupt_payload(self):
        compressed = compress_log(b"ERROR user=1 latency=1ms\n" * 5)
        corrupted = compressed[:5] + bytes(b ^ 0xFF for b in compressed[5:])
        with pytest.raises((ValueError, Exception)):
            decompress_log(corrupted)

    def test_get_compress_mode_corrupt_magic(self):
        compressed = compress_log(b"ERROR user=1 latency=1ms\n" * 5)
        bad = b"XXXX" + compressed[4:]
        with pytest.raises(ValueError, match="magic"):
            get_compress_mode(bad)

    def test_get_compress_mode_truncated(self):
        with pytest.raises(ValueError):
            get_compress_mode(b"\x00\x01")
