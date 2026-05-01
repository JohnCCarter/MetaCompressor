"""Unit tests for MetaCompressor compress → decompress round-trip."""

from __future__ import annotations

import os

import pytest

from metacompressor.compressor import compress
from metacompressor.decompressor import decompress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def round_trip(data: bytes) -> bytes:
    return decompress(compress(data))


# ---------------------------------------------------------------------------
# Core round-trip tests
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_empty_file(self):
        assert round_trip(b"") == b""

    def test_small_file_below_chunk_size(self):
        data = b"Hello, MetaCompressor!"
        assert round_trip(data) == data

    def test_exact_one_chunk(self):
        data = os.urandom(4096)
        assert round_trip(data) == data

    def test_multiple_chunks(self):
        data = os.urandom(4096 * 5 + 123)
        assert round_trip(data) == data

    def test_text_file(self):
        data = ("The quick brown fox jumps over the lazy dog.\n" * 1000).encode()
        assert round_trip(data) == data

    def test_log_like_file(self):
        lines = [
            f"2024-01-01T00:00:{i:02d}Z INFO server started request_id={i}\n"
            for i in range(500)
        ]
        data = "".join(lines).encode()
        assert round_trip(data) == data

    def test_repeated_content(self):
        # Many identical chunks → heavy deduplication
        chunk = b"A" * 4096
        data = chunk * 100
        assert round_trip(data) == data

    def test_binary_data(self):
        data = bytes(range(256)) * 400
        assert round_trip(data) == data

    def test_single_byte(self):
        assert round_trip(b"\xff") == b"\xff"


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        data = b"determinism check " * 300
        assert compress(data) == compress(data)

    def test_different_inputs_differ(self):
        a = compress(b"aaa" * 2000)
        b = compress(b"bbb" * 2000)
        assert a != b


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_repeated_chunks_deduplicated(self):
        chunk = b"X" * 4096
        data = chunk * 50
        mc1 = compress(data)
        # Compressed output must be substantially smaller than raw repeated data
        assert len(mc1) < len(data) // 2

    def test_all_unique_chunks(self):
        data = os.urandom(4096 * 10)
        assert round_trip(data) == data


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_corrupt_magic(self):
        mc1 = compress(b"test data")
        corrupted = b"BAD!" + mc1[4:]
        with pytest.raises(ValueError, match="magic"):
            decompress(corrupted)

    def test_truncated_data(self):
        with pytest.raises(ValueError):
            decompress(b"\x00\x01\x02")

    def test_corrupt_payload(self):
        mc1 = compress(b"test data for corruption")
        # Flip bytes in the compressed payload region
        corrupted = mc1[:5] + bytes(b ^ 0xFF for b in mc1[5:])
        with pytest.raises((ValueError, Exception)):
            decompress(corrupted)
