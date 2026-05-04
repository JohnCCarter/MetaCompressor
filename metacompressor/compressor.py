"""Compressor: input bytes → .mc1 bytes."""

from __future__ import annotations

from metacompressor.container import MC1Container, serialise
from metacompressor.delta import delta_encoded_size, find_similar_chunk
from metacompressor.utils import (
    CDC_AVG_CHUNK_SIZE,
    CDC_MASK,
    CDC_MAX_CHUNK_SIZE,
    CDC_MIN_CHUNK_SIZE,
    CHUNK_SIZE,
    cdc_chunk_data,
    chunk_data,
    hash_chunk,
)

# Valid chunking mode identifiers.
CHUNKING_FIXED = "fixed"
CHUNKING_CDC = "cdc"


def compress(
    data: bytes,
    chunk_size: int = CHUNK_SIZE,
    chunking_mode: str = CHUNKING_FIXED,
    *,
    min_chunk_size: int = CDC_MIN_CHUNK_SIZE,
    avg_chunk_size: int = CDC_AVG_CHUNK_SIZE,
    max_chunk_size: int = CDC_MAX_CHUNK_SIZE,
    cdc_mask: int = CDC_MASK,
) -> bytes:
    """Compress *data* and return the serialised .mc1 byte string.

    Parameters
    ----------
    data:
        Raw bytes to compress.
    chunk_size:
        Fixed chunk size in bytes (used only when *chunking_mode* is
        ``"fixed"``).
    chunking_mode:
        ``"fixed"`` (default) for fixed-size chunking, or ``"cdc"`` for
        content-defined chunking.
    min_chunk_size / avg_chunk_size / max_chunk_size / cdc_mask:
        CDC parameters (used only when *chunking_mode* is ``"cdc"``).

    Steps
    -----
    1. Split *data* into chunks (fixed or CDC).
    2. Hash each chunk with xxhash-64 to obtain its identity.
    3. Build a dictionary mapping hash → (chunk_id, raw bytes), storing
       only the first occurrence of each unique chunk.
    4. For each new unique chunk, attempt delta encoding against recently
       stored full chunks of the same size.  A delta is stored only when it
       is smaller than the raw chunk bytes; otherwise the full chunk is kept.
    5. Build the reference sequence (list of chunk_ids).
    6. Serialise the container and compress with Zstandard.
    """
    if chunking_mode not in (CHUNKING_FIXED, CHUNKING_CDC):
        raise ValueError(
            f"Unknown chunking_mode: {chunking_mode!r}. Expected 'fixed' or 'cdc'."
        )

    hash_to_id: dict[str, int] = {}
    container = MC1Container(
        chunk_size=chunk_size,
        chunking_mode=chunking_mode,
        min_chunk_size=min_chunk_size if chunking_mode == CHUNKING_CDC else None,
        avg_chunk_size=avg_chunk_size if chunking_mode == CHUNKING_CDC else None,
        max_chunk_size=max_chunk_size if chunking_mode == CHUNKING_CDC else None,
        cdc_mask=cdc_mask if chunking_mode == CHUNKING_CDC else None,
    )
    # Insertion-order list of full-chunk IDs (delta base candidates).
    full_id_order: list[int] = []
    next_id = 0

    if chunking_mode == CHUNKING_CDC:
        chunks_iter = cdc_chunk_data(
            data,
            min_size=min_chunk_size,
            avg_size=avg_chunk_size,
            max_size=max_chunk_size,
            mask=cdc_mask,
        )
    else:
        chunks_iter = chunk_data(data, chunk_size)

    for chunk in chunks_iter:
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
