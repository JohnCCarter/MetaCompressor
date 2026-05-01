"""Utility helpers: fixed-size chunking and xxhash-based chunk identity."""

from __future__ import annotations

from typing import Generator

import xxhash

CHUNK_SIZE = 4096


def chunk_data(data: bytes, chunk_size: int = CHUNK_SIZE) -> Generator[bytes, None, None]:
    """Yield successive fixed-size chunks from *data*.

    The last chunk may be smaller than *chunk_size*.
    An empty *data* produces no chunks.
    """
    offset = 0
    length = len(data)
    while offset < length:
        yield data[offset : offset + chunk_size]
        offset += chunk_size


def hash_chunk(chunk: bytes) -> str:
    """Return a hex digest string that uniquely identifies *chunk*."""
    return xxhash.xxh64(chunk).hexdigest()
