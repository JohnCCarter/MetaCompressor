"""Container serialisation / deserialisation for the .mc1 file format.

Binary layout
-------------
[4 bytes] magic  "MC1\\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

Payload (msgpack map)
---------------------
chunking_mode  : str   "fixed" | "cdc"   (absent in pre-CDC files → "fixed")
chunk_size     : int   (fixed mode)
min_chunk_size : int   (cdc mode)
avg_chunk_size : int   (cdc mode)
max_chunk_size : int   (cdc mode)
cdc_mask       : int   (cdc mode)
chunks         : list of [chunk_id: int, data: bytes]  (ordered by chunk_id)
sequence       : list of int  (chunk_ids in original order)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import msgpack
import zstandard as zstd

MAGIC = b"MC1\x00"
VERSION = 0x01
_ZSTD_LEVEL = 3


@dataclass
class MC1Container:
    # Fixed-mode parameter (default matches legacy CHUNK_SIZE)
    chunk_size: int = 4096
    # Chunking mode: "fixed" or "cdc"
    chunking_mode: str = "fixed"
    # CDC parameters (only meaningful when chunking_mode == "cdc")
    min_chunk_size: Optional[int] = None
    avg_chunk_size: Optional[int] = None
    max_chunk_size: Optional[int] = None
    cdc_mask: Optional[int] = None
    # Chunk dictionary and reconstruction sequence
    chunks: Dict[int, bytes] = field(default_factory=dict)   # chunk_id → raw bytes
    sequence: List[int] = field(default_factory=list)        # ordered chunk_ids


def serialise(container: MC1Container) -> bytes:
    """Serialise *container* to a compressed .mc1 byte string."""
    sorted_chunks = sorted(container.chunks.items())
    payload: dict = {
        "chunking_mode": container.chunking_mode,
        "chunks": [[cid, data] for cid, data in sorted_chunks],
        "sequence": container.sequence,
    }

    if container.chunking_mode == "cdc":
        payload["min_chunk_size"] = container.min_chunk_size
        payload["avg_chunk_size"] = container.avg_chunk_size
        payload["max_chunk_size"] = container.max_chunk_size
        payload["cdc_mask"] = container.cdc_mask
    else:
        payload["chunk_size"] = container.chunk_size

    raw = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    compressed = cctx.compress(raw)
    return MAGIC + bytes([VERSION]) + compressed


def deserialise(data: bytes) -> MC1Container:
    """Deserialise a .mc1 byte string into an *MC1Container*.

    Raises ``ValueError`` on any format or integrity error.
    Backward-compatible: pre-CDC files that lack ``chunking_mode`` are
    treated as ``"fixed"`` mode.
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

    # Backward-compat: old files have no "chunking_mode" key → "fixed"
    chunking_mode: str = payload.get("chunking_mode", "fixed")
    chunk_size: int = payload.get("chunk_size", 4096)

    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_data) for cid, chunk_data in payload["chunks"]
    }
    sequence: List[int] = payload["sequence"]

    container = MC1Container(
        chunk_size=chunk_size,
        chunking_mode=chunking_mode,
        chunks=chunks,
        sequence=sequence,
    )

    if chunking_mode == "cdc":
        container.min_chunk_size = payload.get("min_chunk_size")
        container.avg_chunk_size = payload.get("avg_chunk_size")
        container.max_chunk_size = payload.get("max_chunk_size")
        container.cdc_mask = payload.get("cdc_mask")

    return container
