"""Unit tests for MetaCompressor compress → decompress round-trip."""

from __future__ import annotations

import os

import pytest

from metacompressor.compressor import CHUNKING_CDC, CHUNKING_FIXED, compress
from metacompressor.decompressor import decompress

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def round_trip(data: bytes, **kwargs) -> bytes:
    return decompress(compress(data, **kwargs))


def round_trip_cdc(data: bytes) -> bytes:
    return round_trip(data, chunking_mode=CHUNKING_CDC)


# ---------------------------------------------------------------------------
# Core round-trip tests (fixed chunking – unchanged)
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
# Determinism tests (fixed)
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
# Deduplication tests (fixed)
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


# ---------------------------------------------------------------------------
# CDC round-trip tests
# ---------------------------------------------------------------------------


class TestCDCRoundTrip:
    def test_empty_file(self):
        assert round_trip_cdc(b"") == b""

    def test_small_file_below_min_chunk_size(self):
        """File smaller than CDC min_chunk_size must still round-trip."""
        data = b"Hello, CDC MetaCompressor!"
        assert round_trip_cdc(data) == data

    def test_single_byte(self):
        assert round_trip_cdc(b"\xab") == b"\xab"

    def test_multiple_chunks(self):
        data = os.urandom(4096 * 10 + 777)
        assert round_trip_cdc(data) == data

    def test_text_file(self):
        data = ("The quick brown fox jumps over the lazy dog.\n" * 1000).encode()
        assert round_trip_cdc(data) == data

    def test_log_like_file(self):
        lines = [
            f"2024-01-01T00:00:{i:02d}Z INFO server started request_id={i}\n"
            for i in range(500)
        ]
        data = "".join(lines).encode()
        assert round_trip_cdc(data) == data

    def test_repeated_content(self):
        chunk = b"B" * 4096
        data = chunk * 50
        assert round_trip_cdc(data) == data

    def test_binary_data(self):
        data = bytes(range(256)) * 400
        assert round_trip_cdc(data) == data


# ---------------------------------------------------------------------------
# CDC determinism tests
# ---------------------------------------------------------------------------


class TestCDCDeterminism:
    def test_same_input_same_output(self):
        data = b"cdc determinism check " * 300
        c1 = compress(data, chunking_mode=CHUNKING_CDC)
        c2 = compress(data, chunking_mode=CHUNKING_CDC)
        assert c1 == c2

    def test_different_inputs_differ(self):
        a = compress(b"cdc_aaa" * 2000, chunking_mode=CHUNKING_CDC)
        b = compress(b"cdc_bbb" * 2000, chunking_mode=CHUNKING_CDC)
        assert a != b

    def test_fixed_and_cdc_produce_different_containers(self):
        """CDC and fixed outputs must differ (different metadata at minimum)."""
        data = os.urandom(4096 * 5)
        fixed = compress(data, chunking_mode=CHUNKING_FIXED)
        cdc = compress(data, chunking_mode=CHUNKING_CDC)
        assert fixed != cdc


# ---------------------------------------------------------------------------
# CDC metadata tests
# ---------------------------------------------------------------------------


class TestCDCMetadata:
    def test_cdc_mode_stored_in_container(self):
        from metacompressor.container import deserialise

        data = b"x" * 10000
        mc1 = compress(data, chunking_mode=CHUNKING_CDC)
        container = deserialise(mc1)
        assert container.chunking_mode == "cdc"
        assert container.min_chunk_size is not None
        assert container.avg_chunk_size is not None
        assert container.max_chunk_size is not None
        assert container.cdc_mask is not None

    def test_fixed_mode_stored_in_container(self):
        from metacompressor.container import deserialise

        data = b"x" * 10000
        mc1 = compress(data, chunking_mode=CHUNKING_FIXED)
        container = deserialise(mc1)
        assert container.chunking_mode == "fixed"
        assert container.chunk_size == 4096


# ---------------------------------------------------------------------------
# Backward-compatibility test
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_legacy_fixed_file_decompresses(self):
        """A file compressed with the old (no chunking_mode field) API must
        still decompress correctly.  We simulate this by patching the
        serialised payload to remove the 'chunking_mode' key."""
        import msgpack
        import zstandard as zstd

        from metacompressor.container import _ZSTD_LEVEL, MAGIC, VERSION, deserialise

        data = b"legacy round-trip test " * 200
        mc1 = compress(data, chunking_mode=CHUNKING_FIXED)

        c = deserialise(mc1)
        sorted_chunks = sorted(c.chunks.items())
        payload: dict = {
            "chunk_size": c.chunk_size,
            "chunks": [[cid, chunk_b] for cid, chunk_b in sorted_chunks],
            "sequence": c.sequence,
            "chunking_mode": c.chunking_mode,
        }
        if c.delta_chunks:
            payload["delta_chunks"] = [
                [cid, base_cid, target_len, diffs]
                for cid, (base_cid, target_len, diffs) in sorted(c.delta_chunks.items())
            ]
        del payload["chunking_mode"]
        repackaged = msgpack.packb(payload, use_bin_type=True)
        legacy_mc1 = (
            MAGIC
            + bytes([VERSION])
            + zstd.ZstdCompressor(level=_ZSTD_LEVEL).compress(repackaged)
        )

        assert decompress(legacy_mc1) == data
