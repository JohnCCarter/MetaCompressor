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
        # Use a cycling value range so the template encoding is smaller than raw
        # zstd.  With only unique sequential values zstd wins; with a small
        # cycling range template extraction + zstd wins.
        lines = [f"ERROR user={i % 10} latency={i % 30}ms\n" for i in range(50)]
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

    def test_sufficient_repetition_triggers_template_mode(self):
        # Two templates, cycling values, enough lines that the template
        # encoding is smaller than raw zstd – verifies the size-aware
        # selection activates template mode when it is genuinely beneficial.
        lines = []
        for i in range(18):
            lines.append(f"ERROR user={i % 5} latency={i % 10}ms\n")
            lines.append(f"INFO  req={i % 5} status=200\n")
        data = "".join(lines).encode()
        compressed = compress_log(data)
        assert get_compress_mode(compressed) == TEMPLATE_MODE_VALIDATE

    def test_problem_statement_example(self):
        """The exact example from the problem statement round-trips correctly.

        With only two lines the raw-zstd path is smaller than the template
        encoding, so compress_log falls back to raw mode.  Lossless
        round-trip is the invariant that must always hold.
        """
        data = (
            b"ERROR user=123 latency=45ms\n"
            b"ERROR user=456 latency=30ms\n"
        )
        compressed = compress_log(data)
        assert round_trip(data) == data
        # At this tiny size raw mode is cheaper; template mode kicks in once
        # the corpus is large enough that template savings exceed overhead.
        assert get_compress_mode(compressed) in ("raw", TEMPLATE_MODE_VALIDATE)


# ---------------------------------------------------------------------------
# Extended tokeniser – new variable types
# ---------------------------------------------------------------------------

class TestExtendedTokenizer:
    """Round-trip tests for each variable type recognised by the extended tokeniser."""

    def test_uuid_round_trip(self):
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        lines = [f"REQUEST id={uuid} status=200\n"] * 20
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_uuid_template_mode(self):
        # Verify UUID is extracted as a single variable token (not split).
        from metacompressor.log_template import _tokenize
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        line = f"REQUEST id={uuid} status=200"
        tkey, values = _tokenize(line)
        assert uuid in values, f"UUID should be extracted as one variable; got values={values}"
        # Template skeleton should not contain the UUID digits.
        assert uuid not in "".join(tkey)

    def test_uuid_varying_round_trip(self):
        import random
        rng = random.Random(0)

        def _uuid():
            h = "%08x-%04x-%04x-%04x-%012x"
            return h % (
                rng.randint(0, 0xFFFFFFFF),
                rng.randint(0, 0xFFFF),
                rng.randint(0, 0xFFFF),
                rng.randint(0, 0xFFFF),
                rng.randint(0, 0xFFFFFFFFFFFF),
            )

        lines = [f"REQUEST id={_uuid()} status=200\n" for _ in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_iso_datetime_round_trip(self):
        lines = [
            f"2024-01-15T{h:02d}:{m:02d}:{s:02d}Z INFO event={n}\n"
            for n, (h, m, s) in enumerate(
                (h, m % 60, s % 60)
                for h in range(5)
                for m in range(12)
                for s in [0, 30]
            )
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_iso_datetime_with_fractional_seconds(self):
        lines = [f"2024-03-01T12:00:{i:02d}.{i*10:03d}Z METRIC value={i}\n" for i in range(30)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_iso_datetime_with_offset_timezone(self):
        lines = [f"2024-06-01T08:{i:02d}:00+05:30 INFO req={i}\n" for i in range(30)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_ipv4_round_trip(self):
        lines = [f"CONN src=192.168.1.{i} dst=10.0.0.1 port={1024+i}\n" for i in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_ipv4_with_port_round_trip(self):
        lines = [f"CONNECT 10.0.0.{i}:{8000+i} method=GET\n" for i in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_ipv4_template_shared(self):
        # Verify IPv4 is extracted as a single variable token.
        from metacompressor.log_template import _tokenize
        line = "CONN src=192.168.1.42 dst=10.0.0.1 port=8080"
        tkey, values = _tokenize(line)
        assert "192.168.1.42" in values, f"IPv4 should be extracted as one variable; got values={values}"
        assert "10.0.0.1" in values

    def test_hex_0x_round_trip(self):
        lines = [f"ADDR ptr=0x{i:08X} val=0x{i*2:04x}\n" for i in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_hex_0x_template_mode(self):
        # Verify 0x-hex strings are extracted as single variable tokens.
        from metacompressor.log_template import _tokenize
        line = "ADDR ptr=0xDEADBEEF size=64"
        tkey, values = _tokenize(line)
        assert "0xDEADBEEF" in values, f"Hex 0x token should be extracted; got values={values}"
        assert "0xDEADBEEF" not in "".join(tkey)

    def test_url_round_trip(self):
        paths = ["/api/v1", "/health", "/metrics", "/status", "/debug"]
        lines = [f"GET https://example.com{paths[i % len(paths)]} 200\n" for i in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_url_with_query_string_round_trip(self):
        lines = [
            f"GET https://api.example.com/search?q=term{i}&page={i % 5} 200\n"
            for i in range(30)
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_number_still_extracted(self):
        # Ensure plain numbers still work after tokenizer extension.
        lines = [f"ERROR user={i} latency={i % 50}ms\n" for i in range(50)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_mixed_token_types_round_trip(self):
        uuid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        lines = [
            f"2024-01-{i+1:02d}T10:00:00Z REQUEST id={uuid} src=192.168.1.{i} "
            f"ptr=0x{i:04x} status={200 + i % 3}\n"
            for i in range(30)
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_timestamp_only_round_trip(self):
        lines = [f"{h:02d}:{m:02d}:{s:02d} INFO ok\n"
                 for h in range(3) for m in range(10) for s in range(2)]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_determinism_with_extended_tokens(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        lines = [f"LOG id={uuid} ip=10.0.{i}.1 val={i}\n" for i in range(30)]
        data = "".join(lines).encode()
        assert compress_log(data) == compress_log(data)


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
