"""Compressor: input bytes → .mc1 bytes."""

from __future__ import annotations

from metacompressor.container import MC1Container, serialise
from metacompressor.delta import delta_encoded_size, find_similar_chunk
from metacompressor.utils import CHUNK_SIZE, chunk_data, hash_chunk


def compress(data: bytes, chunk_size: int = CHUNK_SIZE) -> bytes:
    """Compress *data* and return the serialised .mc1 byte string.

    Steps
    -----
    1. Split *data* into fixed-size chunks.
    2. Hash each chunk with xxhash-64 to obtain its identity.
    3. Build a dictionary mapping hash → (chunk_id, raw bytes), storing
       only the first occurrence of each unique chunk.
    4. For each new unique chunk, attempt delta encoding against recently
       stored full chunks of the same size.  A delta is stored only when it
       is smaller than the raw chunk bytes; otherwise the full chunk is kept.
    5. Build the reference sequence (list of chunk_ids).
    6. Serialise the container and compress with Zstandard.
    """
    hash_to_id: dict[str, int] = {}
    container = MC1Container(chunk_size=chunk_size)
    # Insertion-order list of full-chunk IDs (delta base candidates).
    full_id_order: list[int] = []
    next_id = 0

    for chunk in chunk_data(data, chunk_size):
        h = hash_chunk(chunk)
        if h not in hash_to_id:
            cid = next_id
            next_id += 1
            hash_to_id[h] = cid

            # Attempt delta encoding against recent full chunks.
            delta_result = find_similar_chunk(chunk, container.chunks, full_id_order)
            if delta_result is not None:
                base_id, diffs = delta_result
                if delta_encoded_size(diffs) < len(chunk):
                    container.delta_chunks[cid] = (base_id, len(chunk), diffs)
                    container.sequence.append(cid)
                    continue

            # Fall back to storing the full chunk.
            container.chunks[cid] = chunk
            full_id_order.append(cid)

        container.sequence.append(hash_to_id[h])

    return serialise(container)
