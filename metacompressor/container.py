"""Container serialisation / deserialisation for the .mc1 and .mc1dir formats.

.mc1 binary layout (single file)
---------------------------------
[4 bytes] magic  "MC1\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed payload

Payload (default): **short-key msgpack** map (``cs``, ``c``, ``s``, …) streamed
into ZSTD without building one giant ``packb`` blob first.  Readers accept
short keys **or** legacy long keys (``chunk_size``, ``chunks``, …).

Optional **MCZ1** experimental layout (see :mod:`metacompressor.zstd_affinity_pack_v1`).

.mc1dir binary layout (multi-file corpus)
------------------------------------------
[4 bytes] magic  "MCD\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed payload (msgpack or ``MCZ1`` affinity)
"""

from __future__ import annotations

import io
from typing import Dict, List

import msgpack
import zstandard as zstd

from metacompressor.mc1_msgpack_short import (
    normalise_mc1_payload_keys,
    normalise_mc1dir_payload_keys,
    write_mc1_msgpack_short_stream,
)
from metacompressor.mc1_types import FileEntry, MC1Container, MC1DirContainer
from metacompressor.zstd_affinity_pack_v1 import (
    MCZ1_MAGIC,
    is_zstd_affinity_v1_payload,
)
from metacompressor.zstd_affinity_pack_v1 import (
    pack_mc1_payload as pack_mc1_payload_affinity,
)
from metacompressor.zstd_affinity_pack_v1 import (
    pack_mc1dir_payload as pack_mc1dir_payload_affinity,
)
from metacompressor.zstd_affinity_pack_v1 import (
    unpack_mc1_payload as unpack_mc1_payload_affinity,
)
from metacompressor.zstd_affinity_pack_v1 import (
    unpack_mc1dir_payload as unpack_mc1dir_payload_affinity,
)

# Public types (re-exported for stable ``from metacompressor.container import``).
__all__ = [
    "FileEntry",
    "MC1Container",
    "MC1DirContainer",
    "MAGIC",
    "MAGIC_DIR",
    "MCZ1_MAGIC",
    "VERSION",
    "VERSION_DIR",
    "_ZSTD_LEVEL",
    "is_zstd_affinity_v1_payload",
    "pack_mc1_payload",
    "pack_mc1dir_payload",
    "pack_mc1_payload_affinity",
    "pack_mc1dir_payload_affinity",
    "pack_mc1_payload_msgpack",
    "pack_mc1dir_payload_msgpack",
    "serialise",
    "serialise_dir",
    "deserialise",
    "deserialise_dir",
]

MAGIC = b"MC1\x00"
VERSION = 0x01
_ZSTD_LEVEL = 3

MAGIC_DIR = b"MCD\x00"
VERSION_DIR = 0x01


def _mc1_payload_dict(container: MC1Container) -> dict:
    sorted_chunks = sorted(container.chunks.items())
    payload: dict = {
        "chunking_mode": container.chunking_mode,
        "chunks": [[cid, data] for cid, data in sorted_chunks],
        "sequence": container.sequence,
    }
    if container.delta_chunks:
        payload["delta_chunks"] = [
            [cid, base_cid, target_len, diffs]
            for cid, (base_cid, target_len, diffs) in sorted(
                container.delta_chunks.items()
            )
        ]
    if container.chunking_mode == "cdc":
        payload["min_chunk_size"] = container.min_chunk_size
        payload["avg_chunk_size"] = container.avg_chunk_size
        payload["max_chunk_size"] = container.max_chunk_size
        payload["cdc_mask"] = container.cdc_mask
    else:
        payload["chunk_size"] = container.chunk_size
    return payload


def pack_mc1_payload_msgpack(container: MC1Container) -> bytes:
    """All-msgpack uncompressed payload (long keys, for tests / comparisons)."""
    return msgpack.packb(_mc1_payload_dict(container), use_bin_type=True)


def pack_mc1_payload(container: MC1Container) -> bytes:
    """Return the uncompressed bytes ZSTD compresses for .mc1 (short-map msgpack)."""
    buf = bytearray()
    write_mc1_msgpack_short_stream(container, buf.extend, single_file=True)
    return bytes(buf)


def _mc1dir_payload_dict(container: MC1DirContainer) -> dict:
    sorted_chunks = sorted(container.chunks.items())
    payload = {
        "chunk_size": container.chunk_size,
        "chunks": [[cid, data] for cid, data in sorted_chunks],
        "files": [{"path": f.path, "sequence": f.sequence} for f in container.files],
    }
    if container.delta_chunks:
        payload["delta_chunks"] = [
            [cid, base_cid, target_len, diffs]
            for cid, (base_cid, target_len, diffs) in sorted(
                container.delta_chunks.items()
            )
        ]
    return payload


def pack_mc1dir_payload_msgpack(container: MC1DirContainer) -> bytes:
    """All-msgpack uncompressed payload (long keys, for tests / comparisons)."""
    return msgpack.packb(_mc1dir_payload_dict(container), use_bin_type=True)


def pack_mc1dir_payload(container: MC1DirContainer) -> bytes:
    """Return the uncompressed bytes ZSTD compresses for .mc1dir (short-map msgpack)."""
    buf = bytearray()
    write_mc1_msgpack_short_stream(container, buf.extend, single_file=False)
    return bytes(buf)


def serialise(container: MC1Container) -> bytes:
    """Serialise *container* to a compressed .mc1 byte string."""
    raw_buf = io.BytesIO()
    write_mc1_msgpack_short_stream(container, raw_buf.write, single_file=True)
    raw = raw_buf.getvalue()
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    return MAGIC + bytes([VERSION]) + cctx.compress(raw)


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

    if is_zstd_affinity_v1_payload(raw):
        return unpack_mc1_payload_affinity(raw)

    payload = normalise_mc1_payload_keys(msgpack.unpackb(raw, raw=False))

    # Backward-compat: old files have no "chunking_mode" key → "fixed"
    chunking_mode: str = payload.get("chunking_mode", "fixed")
    chunk_size: int = payload.get("chunk_size", 4096)

    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_data) for cid, chunk_data in payload["chunks"]
    }
    if "delta_chunks" in payload:
        from metacompressor.delta import apply_delta

        for entry in payload["delta_chunks"]:
            cid, base_cid, target_len, raw_diffs = (
                entry[0],
                entry[1],
                entry[2],
                entry[3],
            )
            if base_cid not in chunks:
                raise ValueError(
                    f"Delta chunk {cid} references unknown base chunk {base_cid}"
                )
            chunks[cid] = apply_delta(chunks[base_cid], raw_diffs, target_len)
    sequence: List[int] = payload["sequence"]
    container = MC1Container(
        chunk_size=chunk_size,
        chunks=chunks,
        sequence=sequence,
        chunking_mode=chunking_mode,
    )
    if chunking_mode == "cdc":
        container.min_chunk_size = payload.get("min_chunk_size")
        container.avg_chunk_size = payload.get("avg_chunk_size")
        container.max_chunk_size = payload.get("max_chunk_size")
        container.cdc_mask = payload.get("cdc_mask")
    return container


def serialise_dir(container: MC1DirContainer) -> bytes:
    """Serialise *container* to a compressed .mc1dir byte string."""
    raw_buf = io.BytesIO()
    write_mc1_msgpack_short_stream(container, raw_buf.write, single_file=False)
    raw = raw_buf.getvalue()
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    return MAGIC_DIR + bytes([VERSION_DIR]) + cctx.compress(raw)


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

    if is_zstd_affinity_v1_payload(raw):
        return unpack_mc1dir_payload_affinity(raw)

    payload = normalise_mc1dir_payload_keys(msgpack.unpackb(raw, raw=False))
    chunk_size = payload["chunk_size"]
    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_bytes) for cid, chunk_bytes in payload["chunks"]
    }
    if "delta_chunks" in payload:
        from metacompressor.delta import apply_delta

        for entry in payload["delta_chunks"]:
            cid, base_cid, target_len, raw_diffs = (
                entry[0],
                entry[1],
                entry[2],
                entry[3],
            )
            if base_cid not in chunks:
                raise ValueError(
                    f"Delta chunk {cid} references unknown base chunk {base_cid}"
                )
            chunks[cid] = apply_delta(chunks[base_cid], raw_diffs, target_len)
    files: List[FileEntry] = [
        FileEntry(path=entry["path"], sequence=list(entry["sequence"]))
        for entry in payload["files"]
    ]
    return MC1DirContainer(chunk_size=chunk_size, chunks=chunks, files=files)
