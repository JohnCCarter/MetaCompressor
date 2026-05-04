"""Utility helpers: fixed-size chunking, CDC chunking, and xxhash-based chunk identity."""

from __future__ import annotations

import hashlib
from collections.abc import Generator

import xxhash

# ---------------------------------------------------------------------------
# Fixed chunking
# ---------------------------------------------------------------------------

CHUNK_SIZE = 4096


def chunk_data(
    data: bytes, chunk_size: int = CHUNK_SIZE
) -> Generator[bytes, None, None]:
    """Yield successive fixed-size chunks from *data*.

    The last chunk may be smaller than *chunk_size*.
    An empty *data* produces no chunks.
    """
    offset = 0
    length = len(data)
    while offset < length:
        yield data[offset : offset + chunk_size]
        offset += chunk_size


# ---------------------------------------------------------------------------
# Content-Defined Chunking (CDC)
# ---------------------------------------------------------------------------

# CDC parameters – keep centralised here so container metadata and chunker
# always agree on defaults.
CDC_MIN_CHUNK_SIZE: int = 2048
CDC_AVG_CHUNK_SIZE: int = 4096
CDC_MAX_CHUNK_SIZE: int = 8192

# Boundary mask: a chunk boundary is detected when (hash & CDC_MASK) == 0.
# With a uniform hash, P(boundary) ≈ 1 / (CDC_MASK + 1) = 1/4096 which
# gives an average chunk size of ~CDC_AVG_CHUNK_SIZE bytes.
CDC_MASK: int = CDC_AVG_CHUNK_SIZE - 1  # 0x0FFF


def _make_gear_table() -> tuple:
    """Return a deterministic 256-entry Gear table of 64-bit integers.

    Each entry is the first 8 bytes of SHA-256("metacompressor-gear-<byte>"),
    which is fixed across platforms and Python versions.
    """
    table = []
    for i in range(256):
        digest = hashlib.sha256(b"metacompressor-gear-" + bytes([i])).digest()
        table.append(int.from_bytes(digest[:8], "big"))
    return tuple(table)


# Module-level constant – computed once at import time.
_GEAR_TABLE: tuple = _make_gear_table()


def cdc_chunk_data(
    data: bytes,
    min_size: int = CDC_MIN_CHUNK_SIZE,
    avg_size: int = CDC_AVG_CHUNK_SIZE,
    max_size: int = CDC_MAX_CHUNK_SIZE,
    mask: int = CDC_MASK,
) -> Generator[bytes, None, None]:
    """Yield variable-size content-defined chunks from *data* using a Gear hash.

    Algorithm
    ---------
    For each potential chunk window [pos, pos+max_size):

    1. Skip the first *min_size* bytes (no boundary possible there).
    2. Slide byte-by-byte, updating ``h = ((h << 1) + gear_table[byte]) & 0xFFFF…``.
    3. Emit a boundary (cut the chunk) when ``h & mask == 0``.
    4. Force a boundary at *max_size* regardless of the hash value.

    The *mask* controls average chunk size: P(hit) ≈ 1 / (mask + 1).

    Parameters
    ----------
    data:
        Raw bytes to chunk.
    min_size:
        Minimum chunk size in bytes; no boundary is tested before this.
    avg_size:
        Target average chunk size.  Used only to derive *mask* when the
        caller does not pass an explicit mask.
    max_size:
        Hard upper bound on chunk size; a forced cut is made here.
    mask:
        Boundary mask applied to the rolling hash.  Defaults to
        ``avg_size - 1`` (requires avg_size to be a power of two).
    """
    n = len(data)
    pos = 0
    gear = _GEAR_TABLE

    while pos < n:
        remaining = n - pos
        if remaining <= min_size:
            # Not enough data left to search for a boundary – emit as-is.
            yield data[pos:]
            return

        limit = min(pos + max_size, n)
        h: int = 0
        boundary = limit  # default: force cut at max_size

        for i in range(pos + min_size, limit):
            h = ((h << 1) + gear[data[i]]) & 0xFFFFFFFFFFFFFFFF
            if h & mask == 0:
                boundary = i + 1  # include the trigger byte in this chunk
                break

        yield data[pos:boundary]
        pos = boundary


# ---------------------------------------------------------------------------
# Chunk identity
# ---------------------------------------------------------------------------


def hash_chunk(chunk: bytes) -> str:
    """Return a hex digest string that uniquely identifies *chunk*."""
    return xxhash.xxh64(chunk).hexdigest()
