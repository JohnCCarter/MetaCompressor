"""Container serialisation / deserialisation for the .mc1 file format.

Binary layout
-------------
[4 bytes] magic  "MC1\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

Payload (msgpack map)
---------------------
chunk_size  : int
chunks      : list of [chunk_id: int, data: bytes]  (ordered by chunk_id)
sequence    : list of int  (chunk_ids in original order)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List

import msgpack
import zstandard as zstd

MAGIC = b"MC1\x00"
VERSION = 0x01
_ZSTD_LEVEL = 3


@dataclass
class MC1Container:
    chunk_size: int
    chunks: Dict[int, bytes] = field(default_factory=dict)   # chunk_id → raw bytes
    sequence: List[int] = field(default_factory=list)        # ordered chunk_ids


def serialise(container: MC1Container) -> bytes:
    """Serialise *container* to a compressed .mc1 byte string."""
    # Build deterministic list of (chunk_id, data) sorted by chunk_id
    sorted_chunks = sorted(container.chunks.items())
    payload = {
        "chunk_size": container.chunk_size,
        "chunks": [[cid, data] for cid, data in sorted_chunks],
        "sequence": container.sequence,
    }
    raw = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    compressed = cctx.compress(raw)
    return MAGIC + bytes([VERSION]) + compressed


def deserialise(data: bytes) -> MC1Container:
    """Deserialise a .mc1 byte string into an *MC1Container*.

    Raises ``ValueError`` on any format or integrity error.
    """
    if len(data) < 5:
        raise ValueError("Data too short to be a valid .mc1 file")
    if data[:4] != MAGIC:
        raise ValueError(f"Invalid magic bytes: {data[:4]!r}")
    version = data[4]
    if version != VERSION:
        raise ValueError(f"Unsupported .mc1 version: {version}")

    dctx = zstd.ZstdDecompressor()
    try:
        raw = dctx.decompress(data[5:])
    except zstd.ZstdError as exc:
        raise ValueError(f"Zstandard decompression failed: {exc}") from exc

    payload = msgpack.unpackb(raw, raw=False)
    chunk_size = payload["chunk_size"]
    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_data) for cid, chunk_data in payload["chunks"]
    }
    sequence: List[int] = payload["sequence"]
    return MC1Container(chunk_size=chunk_size, chunks=chunks, sequence=sequence)
