"""ZSTD-affinity packing v1 for .mc1 / .mc1dir uncompressed payloads.

Single ZSTD stream.  **Layout v2** (byte order tuned for ZSTD): the raw chunk
concatenation comes **first** (right after a tiny header), so the match finder
sees file literals immediately—metadata and deltas follow and do not sit
between repeated chunk payloads.

On the wire after ``MCZ1`` + layout byte:

1. ``u32_le`` chunk_blob_len + **raw** chunk bytes (sorted by ``chunk_id``).
2. ``u32_le`` meta_len + msgpack map (structure only: ids, lengths, sequence,
   paths, CDC ints—no chunk bodies).
3. Binary delta tail (``u32_le`` count, then records) or count ``0``.

Legacy: payloads not starting with ``MCZ1`` are historical all-msgpack maps.

Layout v1 (meta before blob) is no longer emitted; v1 bytes are not accepted.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import msgpack

from metacompressor.mc1_types import FileEntry, MC1Container, MC1DirContainer
from metacompressor.utils import CHUNK_SIZE

MCZ1_MAGIC = b"MCZ1"
_LAYOUT_VERSION = 0x02

_KIND_MC1 = b"m1"
_KIND_MC1DIR = b"md"


def _write_uvarint(buf: bytearray, n: int) -> None:
    if n < 0:
        raise ValueError("uvarint must be non-negative")
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            break


def _read_uvarint(data: bytes, pos: int) -> Tuple[int, int]:
    shift = 0
    val = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if b < 0x80:
            return val, pos
        shift += 7
        if shift > 63:
            raise ValueError("uvarint overflow")
    raise ValueError("truncated uvarint")


def _pack_u32_le(buf: bytearray, n: int) -> None:
    buf.extend((n & 0xFFFFFFFF).to_bytes(4, "little", signed=False))


def _read_u32_le(data: bytes, pos: int) -> Tuple[int, int]:
    if pos + 4 > len(data):
        raise ValueError("truncated u32")
    return int.from_bytes(data[pos : pos + 4], "little", signed=False), pos + 4


def _chunks_from_ids_lens_blob(
    chunk_ids: List[int], chunk_lens: List[int], blob: bytes
) -> Dict[int, bytes]:
    if len(chunk_ids) != len(chunk_lens):
        raise ValueError("chunk_ids and chunk_lens length mismatch")
    chunks: Dict[int, bytes] = {}
    pos = 0
    for cid, ln in zip(chunk_ids, chunk_lens, strict=True):
        if pos + ln > len(blob):
            raise ValueError("chunk blob shorter than declared lengths")
        chunks[cid] = bytes(blob[pos : pos + ln])
        pos += ln
    if pos != len(blob):
        raise ValueError("chunk blob length mismatch")
    return chunks


def _encode_delta_tail(
    buf: bytearray, container: MC1Container | MC1DirContainer
) -> None:
    entries = sorted(container.delta_chunks.items())
    _pack_u32_le(buf, len(entries))
    for cid, (base_cid, target_len, diffs) in entries:
        _pack_u32_le(buf, cid)
        _pack_u32_le(buf, base_cid)
        _pack_u32_le(buf, target_len)
        _pack_u32_le(buf, len(diffs))
        for pair in diffs:
            _write_uvarint(buf, int(pair[0]))
            buf.append(int(pair[1]) & 0xFF)


def _apply_delta_tail(data: bytes, pos: int, chunks: dict[int, bytes]) -> int:
    from metacompressor.delta import apply_delta

    n_delta, pos = _read_u32_le(data, pos)
    for _ in range(n_delta):
        cid, pos = _read_u32_le(data, pos)
        base_cid, pos = _read_u32_le(data, pos)
        target_len, pos = _read_u32_le(data, pos)
        n_pairs, pos = _read_u32_le(data, pos)
        pairs: list[list[int]] = []
        for _p in range(n_pairs):
            off, pos = _read_uvarint(data, pos)
            if pos >= len(data):
                raise ValueError("truncated diff byte")
            bval = data[pos]
            pos += 1
            pairs.append([off, bval])
        if base_cid not in chunks:
            raise ValueError(
                f"Delta chunk {cid} references unknown base chunk {base_cid}"
            )
        chunks[cid] = apply_delta(chunks[base_cid], pairs, target_len)
    return pos


def _meta_mc1(container: MC1Container) -> Dict[str, Any]:
    sorted_chunks = sorted(container.chunks.items())
    meta: Dict[str, Any] = {
        "_k": _KIND_MC1,
        "chunking_mode": container.chunking_mode,
        "chunk_ids": [cid for cid, _ in sorted_chunks],
        "chunk_lens": [len(b) for _, b in sorted_chunks],
        "sequence": container.sequence,
    }
    if container.chunking_mode == "cdc":
        meta["min_chunk_size"] = container.min_chunk_size
        meta["avg_chunk_size"] = container.avg_chunk_size
        meta["max_chunk_size"] = container.max_chunk_size
        meta["cdc_mask"] = container.cdc_mask
    else:
        meta["chunk_size"] = container.chunk_size
    return meta


def _meta_mc1dir(container: MC1DirContainer) -> Dict[str, Any]:
    sorted_chunks = sorted(container.chunks.items())
    return {
        "_k": _KIND_MC1DIR,
        "chunk_size": container.chunk_size,
        "chunk_ids": [cid for cid, _ in sorted_chunks],
        "chunk_lens": [len(b) for _, b in sorted_chunks],
        "files": [{"path": f.path, "sequence": f.sequence} for f in container.files],
    }


def pack_mc1_payload(container: MC1Container) -> bytes:
    """Build ZSTD-affinity uncompressed payload for .mc1 (layout v2)."""
    sorted_chunks = sorted(container.chunks.items())
    blob = b"".join(b for _, b in sorted_chunks)
    meta_bytes = msgpack.packb(_meta_mc1(container), use_bin_type=True)
    buf = bytearray()
    buf.extend(MCZ1_MAGIC)
    buf.append(_LAYOUT_VERSION)
    _pack_u32_le(buf, len(blob))
    buf.extend(blob)
    _pack_u32_le(buf, len(meta_bytes))
    buf.extend(meta_bytes)
    if container.delta_chunks:
        _encode_delta_tail(buf, container)
    else:
        _pack_u32_le(buf, 0)
    return bytes(buf)


def unpack_mc1_payload(raw: bytes) -> MC1Container:
    if len(raw) < 10 or raw[:4] != MCZ1_MAGIC:
        raise ValueError("not a ZSTD-affinity .mc1 payload")
    if raw[4] != _LAYOUT_VERSION:
        raise ValueError(f"unsupported ZSTD-affinity layout version: {raw[4]}")
    pos = 5
    blob_len, pos = _read_u32_le(raw, pos)
    if pos + blob_len > len(raw):
        raise ValueError("truncated chunk blob")
    blob = raw[pos : pos + blob_len]
    pos += blob_len
    meta_len, pos = _read_u32_le(raw, pos)
    if pos + meta_len > len(raw):
        raise ValueError("truncated metadata")
    meta = msgpack.unpackb(raw[pos : pos + meta_len], raw=False)
    pos += meta_len
    if meta.get("_k") != _KIND_MC1:
        raise ValueError("wrong payload kind for .mc1")
    chunks = _chunks_from_ids_lens_blob(
        list(meta["chunk_ids"]), list(meta["chunk_lens"]), blob
    )
    pos = _apply_delta_tail(raw, pos, chunks)
    if pos != len(raw):
        raise ValueError("trailing garbage in ZSTD-affinity payload")

    chunking_mode: str = meta.get("chunking_mode", "fixed")
    sequence: List[int] = list(meta["sequence"])
    if chunking_mode == "cdc":
        return MC1Container(
            chunk_size=CHUNK_SIZE,
            chunks=chunks,
            sequence=sequence,
            chunking_mode="cdc",
            min_chunk_size=meta.get("min_chunk_size"),
            avg_chunk_size=meta.get("avg_chunk_size"),
            max_chunk_size=meta.get("max_chunk_size"),
            cdc_mask=meta.get("cdc_mask"),
        )
    return MC1Container(
        chunk_size=int(meta["chunk_size"]),
        chunks=chunks,
        sequence=sequence,
        chunking_mode="fixed",
    )


def pack_mc1dir_payload(container: MC1DirContainer) -> bytes:
    """Build ZSTD-affinity uncompressed payload for .mc1dir (layout v2)."""
    sorted_chunks = sorted(container.chunks.items())
    blob = b"".join(b for _, b in sorted_chunks)
    meta_bytes = msgpack.packb(_meta_mc1dir(container), use_bin_type=True)
    buf = bytearray()
    buf.extend(MCZ1_MAGIC)
    buf.append(_LAYOUT_VERSION)
    _pack_u32_le(buf, len(blob))
    buf.extend(blob)
    _pack_u32_le(buf, len(meta_bytes))
    buf.extend(meta_bytes)
    if container.delta_chunks:
        _encode_delta_tail(buf, container)
    else:
        _pack_u32_le(buf, 0)
    return bytes(buf)


def unpack_mc1dir_payload(raw: bytes) -> MC1DirContainer:
    if len(raw) < 10 or raw[:4] != MCZ1_MAGIC:
        raise ValueError("not a ZSTD-affinity .mc1dir payload")
    if raw[4] != _LAYOUT_VERSION:
        raise ValueError(f"unsupported ZSTD-affinity layout version: {raw[4]}")
    pos = 5
    blob_len, pos = _read_u32_le(raw, pos)
    if pos + blob_len > len(raw):
        raise ValueError("truncated chunk blob")
    blob = raw[pos : pos + blob_len]
    pos += blob_len
    meta_len, pos = _read_u32_le(raw, pos)
    if pos + meta_len > len(raw):
        raise ValueError("truncated metadata")
    meta = msgpack.unpackb(raw[pos : pos + meta_len], raw=False)
    pos += meta_len
    if meta.get("_k") != _KIND_MC1DIR:
        raise ValueError("wrong payload kind for .mc1dir")
    chunks = _chunks_from_ids_lens_blob(
        list(meta["chunk_ids"]), list(meta["chunk_lens"]), blob
    )
    pos = _apply_delta_tail(raw, pos, chunks)
    if pos != len(raw):
        raise ValueError("trailing garbage in ZSTD-affinity payload")
    files: List[FileEntry] = [
        FileEntry(path=entry["path"], sequence=list(entry["sequence"]))
        for entry in meta["files"]
    ]
    return MC1DirContainer(
        chunk_size=int(meta["chunk_size"]),
        chunks=chunks,
        files=files,
    )


def is_zstd_affinity_v1_payload(raw: bytes) -> bool:
    return len(raw) >= 4 and raw[:4] == MCZ1_MAGIC


def payload_includes_delta_chunks(raw: bytes) -> bool:
    """True if *raw* (uncompressed ZSTD body) stores delta records (v1 or msgpack)."""
    if not raw:
        return False
    if raw[:4] == MCZ1_MAGIC:
        if len(raw) < 13 or raw[4] != _LAYOUT_VERSION:
            return False
        pos = 5
        blob_len, pos = _read_u32_le(raw, pos)
        pos += blob_len
        if pos + 4 > len(raw):
            return False
        meta_len, pos = _read_u32_le(raw, pos)
        pos += meta_len
        if pos + 4 > len(raw):
            return False
        n_delta, _ = _read_u32_le(raw, pos)
        return n_delta > 0
    try:
        payload = msgpack.unpackb(raw, raw=False)
    except Exception:
        return False
    return "delta_chunks" in payload and len(payload.get("delta_chunks", [])) > 0
