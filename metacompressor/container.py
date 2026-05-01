"""Container serialisation / deserialisation for the .mc1 and .mc1dir formats.

.mc1 binary layout (single file)
---------------------------------
[4 bytes] magic  "MC1\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

.mc1 payload (msgpack map)
--------------------------
chunk_size  : int
chunks      : list of [chunk_id: int, data: bytes]  (ordered by chunk_id)
sequence    : list of int  (chunk_ids in original order)

.mc1dir binary layout (multi-file corpus)
------------------------------------------
[4 bytes] magic  "MCD\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

.mc1dir payload (msgpack map)
------------------------------
chunk_size  : int
chunks      : list of [chunk_id: int, data: bytes]  (shared dictionary, ordered by chunk_id)
files       : list of {path: str, sequence: list of int}  (per-file sequences, path is relative)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import msgpack
import zstandard as zstd

MAGIC = b"MC1\x00"
VERSION = 0x01
_ZSTD_LEVEL = 3

MAGIC_DIR = b"MCD\x00"
VERSION_DIR = 0x01


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


# ---------------------------------------------------------------------------
# Multi-file corpus container (.mc1dir)
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    """One file stored inside a .mc1dir archive."""
    path: str                   # relative POSIX path
    sequence: List[int]         # chunk_ids in original order


@dataclass
class MC1DirContainer:
    chunk_size: int
    chunks: Dict[int, bytes] = field(default_factory=dict)  # shared chunk dict
    files: List[FileEntry] = field(default_factory=list)    # per-file entries


def serialise_dir(container: MC1DirContainer) -> bytes:
    """Serialise *container* to a compressed .mc1dir byte string."""
    sorted_chunks = sorted(container.chunks.items())
    payload = {
        "chunk_size": container.chunk_size,
        "chunks": [[cid, data] for cid, data in sorted_chunks],
        "files": [
            {"path": f.path, "sequence": f.sequence}
            for f in container.files
        ],
    }
    raw = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    compressed = cctx.compress(raw)
    return MAGIC_DIR + bytes([VERSION_DIR]) + compressed


def deserialise_dir(data: bytes) -> MC1DirContainer:
    """Deserialise a .mc1dir byte string into an *MC1DirContainer*.

    Raises ``ValueError`` on any format or integrity error.
    """
    if len(data) < 5:
        raise ValueError("Data too short to be a valid .mc1dir file")
    if data[:4] != MAGIC_DIR:
        raise ValueError(f"Invalid magic bytes: {data[:4]!r}")
    version = data[4]
    if version != VERSION_DIR:
        raise ValueError(f"Unsupported .mc1dir version: {version}")

    dctx = zstd.ZstdDecompressor()
    try:
        raw = dctx.decompress(data[5:])
    except zstd.ZstdError as exc:
        raise ValueError(f"Zstandard decompression failed: {exc}") from exc

    payload = msgpack.unpackb(raw, raw=False)
    chunk_size = payload["chunk_size"]
    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_bytes) for cid, chunk_bytes in payload["chunks"]
    }
    files: List[FileEntry] = [
        FileEntry(path=entry["path"], sequence=list(entry["sequence"]))
        for entry in payload["files"]
    ]
    return MC1DirContainer(chunk_size=chunk_size, chunks=chunks, files=files)
