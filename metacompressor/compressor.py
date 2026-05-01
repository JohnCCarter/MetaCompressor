"""Compressor: input bytes → .mc1 bytes."""

from __future__ import annotations

from metacompressor.container import MC1Container, CorpusContainer, CorpusFile, serialise, serialise_corpus
from metacompressor.utils import (
    CHUNK_SIZE,
    CDC_MIN_CHUNK_SIZE,
    CDC_AVG_CHUNK_SIZE,
    CDC_MAX_CHUNK_SIZE,
    CDC_MASK,
    chunk_data,
    cdc_chunk_data,
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
    4. Build the reference sequence (list of chunk_ids).
    5. Serialise the container and compress with Zstandard.
    """
    if chunking_mode not in (CHUNKING_FIXED, CHUNKING_CDC):
        raise ValueError(f"Unknown chunking_mode: {chunking_mode!r}. "
                         f"Expected 'fixed' or 'cdc'.")

    hash_to_id: dict[str, int] = {}
    container = MC1Container(
        chunk_size=chunk_size,
        chunking_mode=chunking_mode,
        min_chunk_size=min_chunk_size if chunking_mode == CHUNKING_CDC else None,
        avg_chunk_size=avg_chunk_size if chunking_mode == CHUNKING_CDC else None,
        max_chunk_size=max_chunk_size if chunking_mode == CHUNKING_CDC else None,
        cdc_mask=cdc_mask if chunking_mode == CHUNKING_CDC else None,
    )
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
            hash_to_id[h] = next_id
            container.chunks[next_id] = chunk
            next_id += 1
        container.sequence.append(hash_to_id[h])

    return serialise(container)


def compress_corpus(
    files: list[tuple[str, bytes]],
    chunk_size: int = CHUNK_SIZE,
    chunking_mode: str = CHUNKING_FIXED,
    *,
    min_chunk_size: int = CDC_MIN_CHUNK_SIZE,
    avg_chunk_size: int = CDC_AVG_CHUNK_SIZE,
    max_chunk_size: int = CDC_MAX_CHUNK_SIZE,
    cdc_mask: int = CDC_MASK,
) -> bytes:
    """Compress a corpus of *(path, data)* pairs using a shared chunk dictionary.

    Parameters
    ----------
    files:
        Iterable of ``(relative_path, raw_bytes)`` pairs.  Paths must be
        POSIX-style relative paths (no leading ``/``, no ``..`` components).
        Files are sorted by path so output is deterministic regardless of
        the order they are passed in.
    chunk_size / chunking_mode / min_chunk_size / …:
        Same semantics as :func:`compress`.

    Returns
    -------
    bytes
        A corpus .mc1 byte string (version ``0x02``).
    """
    if chunking_mode not in (CHUNKING_FIXED, CHUNKING_CDC):
        raise ValueError(f"Unknown chunking_mode: {chunking_mode!r}. "
                         f"Expected 'fixed' or 'cdc'.")

    # Sort by path for deterministic output.
    sorted_files = sorted(files, key=lambda item: item[0])

    hash_to_id: dict[str, int] = {}
    container = CorpusContainer(
        chunking_mode=chunking_mode,
        chunk_size=chunk_size,
        min_chunk_size=min_chunk_size if chunking_mode == CHUNKING_CDC else None,
        avg_chunk_size=avg_chunk_size if chunking_mode == CHUNKING_CDC else None,
        max_chunk_size=max_chunk_size if chunking_mode == CHUNKING_CDC else None,
        cdc_mask=cdc_mask if chunking_mode == CHUNKING_CDC else None,
    )
    next_id = 0

    for path, data in sorted_files:
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

        sequence: list[int] = []
        for chunk in chunks_iter:
            h = hash_chunk(chunk)
            if h not in hash_to_id:
                hash_to_id[h] = next_id
                container.chunks[next_id] = chunk
                next_id += 1
            sequence.append(hash_to_id[h])

        container.files.append(CorpusFile(path=path, sequence=sequence))

    return serialise_corpus(container)
