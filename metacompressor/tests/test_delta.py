"""Tests for intra-chunk delta encoding."""

from __future__ import annotations

import os

import pytest

from metacompressor.compressor import compress
from metacompressor.container import deserialise, deserialise_dir
from metacompressor.corpus import compress_corpus, decompress_corpus
from metacompressor.decompressor import decompress
from metacompressor.delta import (
    apply_delta,
    compute_delta,
    delta_encoded_size,
    find_similar_chunk,
    similarity,
)
from metacompressor.utils import CHUNK_SIZE


# ---------------------------------------------------------------------------
# Unit tests for delta.py primitives
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical(self):
        a = b"hello world"
        assert similarity(a, a) == 1.0

    def test_completely_different(self):
        a = bytes(range(256))
        b = bytes(reversed(range(256)))
        # Each byte swapped – roughly half identical at most positions
        assert similarity(a, b) < 0.1

    def test_partial(self):
        base = b"\x00" * 100
        target = b"\x00" * 90 + b"\xff" * 10
        assert similarity(base, target) == pytest.approx(0.90)

    def test_empty_returns_zero(self):
        assert similarity(b"", b"") == 0.0

    def test_different_lengths_returns_zero(self):
        assert similarity(b"abc", b"abcd") == 0.0


class TestComputeAndApplyDelta:
    def test_no_differences(self):
        base = b"same data"
        diffs = compute_delta(base, base)
        assert diffs == []
        assert apply_delta(base, diffs, len(base)) == base

    def test_single_byte_change(self):
        base = b"hello"
        target = b"hxllo"
        diffs = compute_delta(base, target)
        assert diffs == [[1, ord("x")]]
        assert apply_delta(base, diffs, len(target)) == target

    def test_multiple_changes(self):
        base = b"abcdef"
        target = b"aXcYef"
        diffs = compute_delta(base, target)
        assert [1, ord("X")] in diffs
        assert [3, ord("Y")] in diffs
        assert apply_delta(base, diffs, len(target)) == target

    def test_target_shorter_than_base(self):
        base = b"hello world"
        target = b"hello"
        diffs = compute_delta(base, target)
        result = apply_delta(base, diffs, len(target))
        assert result == target

    def test_roundtrip_random(self):
        base = os.urandom(CHUNK_SIZE)
        # Mutate 5% of bytes
        target = bytearray(base)
        for i in range(0, len(target), 20):
            target[i] = (target[i] + 1) % 256
        target = bytes(target)
        diffs = compute_delta(base, target)
        assert apply_delta(base, diffs, len(target)) == target


class TestDeltaEncodedSize:
    def test_empty_diffs_small(self):
        assert delta_encoded_size([]) < 10

    def test_size_increases_with_more_diffs(self):
        diffs_few = [[i, 42] for i in range(10)]
        diffs_many = [[i, 42] for i in range(100)]
        assert delta_encoded_size(diffs_few) < delta_encoded_size(diffs_many)


class TestFindSimilarChunk:
    def test_finds_similar(self):
        base = bytes(range(256)) * 16   # 4096 bytes
        target = bytearray(base)
        # Change 5% of bytes
        for i in range(0, 4096, 20):
            target[i] = (target[i] + 1) % 256
        target = bytes(target)

        full_chunks = {0: base}
        result = find_similar_chunk(target, full_chunks, [0], threshold=0.80)
        assert result is not None
        base_id, diffs = result
        assert base_id == 0
        assert apply_delta(base, diffs, len(target)) == target

    def test_returns_none_for_dissimilar(self):
        base = os.urandom(CHUNK_SIZE)
        target = os.urandom(CHUNK_SIZE)
        full_chunks = {0: base}
        result = find_similar_chunk(target, full_chunks, [0], threshold=0.80)
        assert result is None

    def test_returns_none_for_different_lengths(self):
        base = b"x" * 4096
        target = b"x" * 2048   # different size
        full_chunks = {0: base}
        result = find_similar_chunk(target, full_chunks, [0])
        assert result is None

    def test_empty_candidates(self):
        chunk = b"x" * 100
        assert find_similar_chunk(chunk, {}, []) is None

    def test_prefers_most_similar(self):
        """Among multiple candidates the one with highest similarity wins."""
        n = CHUNK_SIZE
        base_good = b"\xAA" * n
        base_poor = b"\x00" * n
        # target: 98% identical to base_good, ~50% to base_poor
        target = bytearray(base_good)
        for i in range(0, n, 50):
            target[i] = 0xFF
        target = bytes(target)

        full_chunks = {0: base_poor, 1: base_good}
        result = find_similar_chunk(target, full_chunks, [0, 1], threshold=0.80)
        assert result is not None
        assert result[0] == 1  # should prefer the better match


# ---------------------------------------------------------------------------
# Integration: delta round-trips through compress / decompress
# ---------------------------------------------------------------------------

class TestDeltaRoundTrip:
    def test_similar_chunks_round_trip(self):
        """Data where consecutive chunks differ by a few bytes decompresses correctly."""
        chunk_size = CHUNK_SIZE
        base = b"\xAB" * chunk_size
        # Build several chunks that each differ from the previous by one byte.
        chunks = [base]
        for i in range(1, 20):
            mutated = bytearray(base)
            mutated[i * 10] = i
            chunks.append(bytes(mutated))
        data = b"".join(chunks)
        assert decompress(compress(data)) == data

    def test_log_like_similar_chunks_round_trip(self):
        """Log-like data with slightly varying timestamps round-trips correctly."""
        lines = [
            f"2024-01-01T00:{i // 60:02d}:{i % 60:02d}Z INFO req={i} path=/api\n"
            for i in range(2000)
        ]
        data = "".join(lines).encode()
        assert decompress(compress(data)) == data

    def test_partial_chunk_delta_round_trip(self):
        """Final partial chunk that is similar to a prior chunk round-trips."""
        chunk_size = CHUNK_SIZE
        base = b"\xCC" * chunk_size
        partial = b"\xCC" * (chunk_size - 100) + b"\xDD" * 100
        # partial is 97.5% identical to base → should delta-encode
        data = base + partial
        assert decompress(compress(data)) == data


# ---------------------------------------------------------------------------
# Delta encoding actually fires (container inspection)
# ---------------------------------------------------------------------------

class TestDeltaEncoded:
    def _similar_data(self, n_chunks: int = 20) -> bytes:
        """Return data whose chunks are all 95%+ similar to the first one."""
        base = b"\xAB" * CHUNK_SIZE
        chunks = [base]
        for i in range(1, n_chunks):
            mutated = bytearray(base)
            mutated[i] = (i + 1) & 0xFF
            chunks.append(bytes(mutated))
        return b"".join(chunks)

    def test_delta_chunks_present_in_container(self):
        """At least one delta chunk should appear for highly-similar data."""
        data = self._similar_data()
        mc1 = compress(data)
        container = deserialise(mc1)
        # deserialise resolves deltas into chunks; verify by checking the raw payload
        import zstandard as zstd
        import msgpack
        raw = zstd.ZstdDecompressor().decompress(mc1[5:])
        payload = msgpack.unpackb(raw, raw=False)
        assert "delta_chunks" in payload, "Expected delta_chunks in payload for similar data"
        assert len(payload["delta_chunks"]) > 0

    def test_delta_reduces_raw_payload_size(self):
        """For highly similar chunks, delta encoding should reduce the raw payload."""
        data = self._similar_data(30)

        # Compress with delta (default)
        mc1_with_delta = compress(data)

        # Compress without delta by using random data of same size (all unique)
        # — just verify the similar data is smaller than random data of same size
        random_data = os.urandom(len(data))
        mc1_random = compress(random_data)

        # Similar data with deltas should compress much better than random
        assert len(mc1_with_delta) < len(mc1_random)


# ---------------------------------------------------------------------------
# Delta encoding: fallback to full chunk when delta is larger
# ---------------------------------------------------------------------------

class TestDeltaFallback:
    def test_random_data_no_delta_chunks(self):
        """Purely random data has no similar chunks; no deltas should appear."""
        data = os.urandom(CHUNK_SIZE * 20)
        mc1 = compress(data)

        import zstandard as zstd
        import msgpack
        raw = zstd.ZstdDecompressor().decompress(mc1[5:])
        payload = msgpack.unpackb(raw, raw=False)
        assert "delta_chunks" not in payload or len(payload.get("delta_chunks", [])) == 0

    def test_fallback_preserves_round_trip(self):
        """Even with mixed similar and dissimilar chunks the round-trip is correct."""
        similar_part = b"\xAA" * CHUNK_SIZE
        for i in range(10):
            mutated = bytearray(similar_part)
            mutated[i] = 0xFF
            similar_part += bytes(mutated)
        random_part = os.urandom(CHUNK_SIZE * 5)
        data = similar_part + random_part
        assert decompress(compress(data)) == data


# ---------------------------------------------------------------------------
# Corpus delta round-trips
# ---------------------------------------------------------------------------

class TestCorpusDelta:
    def test_similar_files_round_trip(self, tmp_path):
        """Corpus with many near-identical files decompresses correctly."""
        base_content = b"\xBB" * CHUNK_SIZE * 4
        files: dict[str, bytes] = {}
        for i in range(10):
            mutated = bytearray(base_content)
            mutated[i * 100] = i
            files[f"f{i}.bin"] = bytes(mutated)

        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        for name, content in files.items():
            (corpus_dir / name).write_bytes(content)

        archive = compress_corpus(corpus_dir)
        out_dir = tmp_path / "out"
        decompress_corpus(archive, out_dir)

        for name, content in files.items():
            assert (out_dir / name).read_bytes() == content

    def test_corpus_delta_chunks_present(self, tmp_path):
        """Delta chunks appear in the .mc1dir payload for similar-file corpus."""
        base_content = b"\xCC" * CHUNK_SIZE * 3
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        for i in range(8):
            mutated = bytearray(base_content)
            mutated[i * 50] = i + 1
            (corpus_dir / f"f{i}.bin").write_bytes(bytes(mutated))

        archive = compress_corpus(corpus_dir)

        import zstandard as zstd
        import msgpack
        raw = zstd.ZstdDecompressor().decompress(archive[5:])
        payload = msgpack.unpackb(raw, raw=False)
        assert "delta_chunks" in payload
        assert len(payload["delta_chunks"]) > 0

    def test_corpus_delta_improves_compression(self, tmp_path):
        """Delta encoding compresses a corpus of similar-but-distinct binary files
        better than compressing each file independently with ZSTD.

        Each file shares a large random base (incompressible by ZSTD alone) with
        only a few bytes changed, so per-file ZSTD cannot exploit cross-file
        similarity while MC delta encoding can.
        """
        import zstandard as zstd_mod

        base_content = os.urandom(CHUNK_SIZE * 8)  # 32 KB of random bytes
        files: dict[str, bytes] = {}
        for i in range(6):
            mutated = bytearray(base_content)
            # Mutate ~5% of bytes — still >95% similar to base
            for j in range(0, CHUNK_SIZE * 8, 20):
                mutated[j] = (mutated[j] + i + 1) % 256
            files[f"f{i}.bin"] = bytes(mutated)

        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        for name, content in files.items():
            (corpus_dir / name).write_bytes(content)

        archive = compress_corpus(corpus_dir)

        cctx = zstd_mod.ZstdCompressor(level=3)
        zstd_total = sum(len(cctx.compress(d)) for d in files.values())

        # MC with delta encoding captures cross-file similarity; per-file ZSTD cannot.
        assert len(archive) < zstd_total


# ---------------------------------------------------------------------------
# Determinism with delta encoding
# ---------------------------------------------------------------------------

class TestDeltaDeterminism:
    def test_same_similar_data_same_output(self):
        base = b"\xDD" * CHUNK_SIZE
        chunks = [base]
        for i in range(1, 15):
            m = bytearray(base)
            m[i] = i
            chunks.append(bytes(m))
        data = b"".join(chunks)
        assert compress(data) == compress(data)

    def test_corpus_delta_deterministic(self, tmp_path):
        base = b"\xEE" * CHUNK_SIZE * 2
        files = {}
        for i in range(5):
            m = bytearray(base)
            m[i * 20] = i + 1
            files[f"f{i}.bin"] = bytes(m)

        def build_and_compress(run_dir):
            d = tmp_path / run_dir / "corpus"
            d.mkdir(parents=True)
            for n, c in files.items():
                (d / n).write_bytes(c)
            return compress_corpus(d)

        assert build_and_compress("run1") == build_and_compress("run2")
