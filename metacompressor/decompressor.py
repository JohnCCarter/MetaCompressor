"""Decompressor: .mc1 bytes → original bytes."""

from __future__ import annotations

from metacompressor.container import deserialise


def decompress(mc1_data: bytes) -> bytes:
    """Decompress *mc1_data* and return the original byte string.

    Raises ``ValueError`` if the container is corrupt or a referenced
    chunk_id is missing from the dictionary.
    """
    container = deserialise(mc1_data)
    parts: list[bytes] = []
    for cid in container.sequence:
        if cid not in container.chunks:
            raise ValueError(
                f"Corrupt container: chunk_id {cid} not found in dictionary"
            )
        parts.append(container.chunks[cid])
    return b"".join(parts)
