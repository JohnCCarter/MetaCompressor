"""Compressor: input bytes → .mc1 bytes."""

from __future__ import annotations

from metacompressor.container import MC1Container, serialise
from metacompressor.utils import CHUNK_SIZE, chunk_data, hash_chunk


def compress(data: bytes, chunk_size: int = CHUNK_SIZE) -> bytes:
    """Compress *data* and return the serialised .mc1 byte string.

    Steps
    -----
    1. Split *data* into fixed-size chunks.
    2. Hash each chunk with xxhash-64 to obtain its identity.
    3. Build a dictionary mapping hash → (chunk_id, raw bytes), storing
       only the first occurrence of each unique chunk.
    4. Build the reference sequence (list of chunk_ids).
    5. Serialise the container and compress with Zstandard.
    """
    hash_to_id: dict[str, int] = {}
    container = MC1Container(chunk_size=chunk_size)
    next_id = 0

    for chunk in chunk_data(data, chunk_size):
        h = hash_chunk(chunk)
        if h not in hash_to_id:
            hash_to_id[h] = next_id
            container.chunks[next_id] = chunk
            next_id += 1
        container.sequence.append(hash_to_id[h])

    return serialise(container)
