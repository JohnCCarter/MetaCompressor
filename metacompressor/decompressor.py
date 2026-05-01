"""Decompressor: .mc1 bytes → original bytes."""

from __future__ import annotations

from metacompressor.container import deserialise, deserialise_corpus


def decompress(mc1_data: bytes) -> bytes:
    """Decompress *mc1_data* and return the original byte string.

    Raises ``ValueError`` if the container is corrupt or a referenced
    chunk_id is missing from the dictionary.
    """
    container = deserialise(mc1_data)
    parts: list[bytes] = []
    for cid in container.sequence:
        if cid not in container.chunks:
            raise ValueError(f"Corrupt container: chunk_id {cid} not found in dictionary")
        parts.append(container.chunks[cid])
    return b"".join(parts)


def decompress_corpus(mc1_data: bytes) -> list[tuple[str, bytes]]:
    """Decompress a corpus .mc1 byte string and return all files.

    Returns
    -------
    list of (relative_path, raw_bytes)
        Files are returned in the order they were stored (sorted by path).

    Raises ``ValueError`` if the container is corrupt.
    """
    container = deserialise_corpus(mc1_data)
    result: list[tuple[str, bytes]] = []
    for f in container.files:
        parts: list[bytes] = []
        for cid in f.sequence:
            if cid not in container.chunks:
                raise ValueError(
                    f"Corrupt corpus: chunk_id {cid} not found in shared dictionary"
                )
            parts.append(container.chunks[cid])
        result.append((f.path, b"".join(parts)))
    return result
