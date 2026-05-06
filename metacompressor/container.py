"""Container serialisation / deserialisation for the .mc1 and .mc1dir formats.

.mc1 binary layout (single file)
---------------------------------
[4 bytes] magic  "MC1\x00"
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

.mc1 payload (msgpack map)
--------------------------
chunking_mode  : str   "fixed" | "cdc"   (absent in pre-CDC files → "fixed")
chunk_size     : int   (fixed mode)
min_chunk_size : int   (cdc mode)
avg_chunk_size : int   (cdc mode)
max_chunk_size : int   (cdc mode)
cdc_mask       : int   (cdc mode)
chunks         : list of [chunk_id: int, data: bytes]  (ordered by chunk_id)
sequence       : list of int  (chunk_ids in original order)
delta_chunks   : list of [cid, base_cid, target_len, diffs]  (optional)

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

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import msgpack
import zstandard as zstd


def _zstd_threads() -> int:
    """Return the configured ZSTD thread count.

    Set ``MC_ZSTD_THREADS=-1`` to use all CPU cores; useful for multi-MB
    payloads on large corpora.  Defaults to 0 (single-threaded) because
    threading adds overhead that hurts MC's typical KB-scale per-archive
    payloads.
    """
    try:
        return int(os.environ.get("MC_ZSTD_THREADS", "0"))
    except ValueError:
        return 0


MAGIC = b"MC1\x00"
VERSION = 0x01
# Level 1 is intentional: by the time bytes reach this serialiser, MC has
# already deduplicated chunks via xxhash and (optionally) delta-encoded
# near-duplicates.  The remaining payload is a msgpack of unique chunks plus
# integer sequences — there is little structure left for ZSTD to discover, so
# the higher search effort of level 3 is mostly wasted CPU.  A trained ZSTD
# dictionary (added for .mc1dir below) recovers any small ratio loss.
_ZSTD_LEVEL = 1

MAGIC_DIR = b"MCD\x00"
VERSION_DIR = 0x01

# ZSTD dictionary tuning for .mc1dir.  The dictionary primes ZSTD with a
# representative sample of the deduplicated chunks so the decoder can match
# against them without ZSTD having to rediscover repeated structure on its
# own.  Skipping when corpora are small avoids spending bytes on a dict that
# won't pay back.
_DICT_MIN_TOTAL_BYTES = 32 * 1024  # below this, dict overhead > savings
_DICT_MIN_SAMPLES = 8
_DICT_MAX_SAMPLES = 128
_DICT_MAX_SIZE = 64 * 1024


def _train_chunk_dict(chunk_bytes_list: List[bytes]) -> Optional[bytes]:
    """Train a ZSTD dictionary on *chunk_bytes_list* or return ``None``.

    Returns ``None`` when the corpus is too small to benefit, so callers can
    fall through to the no-dict compression path with no extra branches.
    """
    if len(chunk_bytes_list) < _DICT_MIN_SAMPLES:
        return None
    total = sum(len(c) for c in chunk_bytes_list)
    if total < _DICT_MIN_TOTAL_BYTES:
        return None
    samples = chunk_bytes_list
    if len(samples) > _DICT_MAX_SAMPLES:
        step = len(samples) // _DICT_MAX_SAMPLES
        samples = samples[::step][:_DICT_MAX_SAMPLES]
    target_size = min(_DICT_MAX_SIZE, max(1024, total // 20))
    try:
        cdict = zstd.train_dictionary(target_size, samples)
    except zstd.ZstdError:
        return None
    return cdict.as_bytes()


def _encode_chunks_blob(sorted_chunks: List) -> bytes:
    """Encode ``[(cid, data), ...]`` as a contiguous binary blob.

    Layout::

        [u32 BE count] [ u32 BE cid | u32 BE len | bytes data ] *

    The point of bypassing msgpack is to give ZSTD a continuous run of
    chunk bytes — no per-chunk type/length tags fragmenting its match
    window.  ZSTD (especially with a trained dictionary) compresses this
    substrate noticeably better than the per-chunk-tagged msgpack list.
    """
    parts: List[bytes] = [len(sorted_chunks).to_bytes(4, "big")]
    for cid, data in sorted_chunks:
        parts.append(int(cid).to_bytes(4, "big"))
        parts.append(len(data).to_bytes(4, "big"))
        parts.append(bytes(data))
    return b"".join(parts)


def _decode_chunks_blob(blob: bytes) -> List:
    """Inverse of :func:`_encode_chunks_blob`."""
    if len(blob) < 4:
        raise ValueError("chunks_blob too short to contain a count header")
    count = int.from_bytes(blob[:4], "big")
    out: List = []
    pos = 4
    for _ in range(count):
        if pos + 8 > len(blob):
            raise ValueError("chunks_blob truncated in chunk header")
        cid = int.from_bytes(blob[pos : pos + 4], "big")
        clen = int.from_bytes(blob[pos + 4 : pos + 8], "big")
        pos += 8
        if pos + clen > len(blob):
            raise ValueError("chunks_blob truncated in chunk body")
        out.append((cid, blob[pos : pos + clen]))
        pos += clen
    if pos != len(blob):
        raise ValueError("chunks_blob has trailing bytes")
    return out


@dataclass
class MC1Container:
    chunk_size: int
    chunks: Dict[int, bytes] = field(
        default_factory=dict
    )  # chunk_id → raw bytes (full chunks)
    sequence: List[int] = field(default_factory=list)  # ordered chunk_ids
    # chunk_id → (base_chunk_id, target_len, [[offset, byte], …])
    delta_chunks: Dict[int, tuple] = field(default_factory=dict)
    # CDC parameters (only meaningful when chunking_mode == "cdc")
    chunking_mode: str = "fixed"
    min_chunk_size: Optional[int] = None
    avg_chunk_size: Optional[int] = None
    max_chunk_size: Optional[int] = None
    cdc_mask: Optional[int] = None


def serialise(container: MC1Container) -> bytes:
    """Serialise *container* to a compressed .mc1 byte string."""
    sorted_chunks = sorted(container.chunks.items())
    payload: dict = {
        "chunking_mode": container.chunking_mode,
        "chunks_blob": _encode_chunks_blob(sorted_chunks),
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
    raw = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL, threads=_zstd_threads())
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

    if "chunks_blob" in payload:
        chunk_records = _decode_chunks_blob(bytes(payload["chunks_blob"]))
    else:
        chunk_records = payload["chunks"]
    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_data) for cid, chunk_data in chunk_records
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


# ---------------------------------------------------------------------------
# Multi-file corpus container (.mc1dir)
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    """One file stored inside a .mc1dir archive."""

    path: str  # relative POSIX path
    sequence: List[int]  # chunk_ids in original order


@dataclass
class MC1DirContainer:
    chunk_size: int
    chunks: Dict[int, bytes] = field(default_factory=dict)  # shared full chunk dict
    files: List[FileEntry] = field(default_factory=list)  # per-file entries
    # chunk_id → (base_chunk_id, target_len, [[offset, byte], …])
    delta_chunks: Dict[int, tuple] = field(default_factory=dict)


def serialise_dir(container: MC1DirContainer) -> bytes:
    """Serialise *container* to a compressed .mc1dir byte string.

    Format note: when there are enough unique chunks to make it worthwhile,
    a ZSTD dictionary trained on the chunk samples is embedded as the
    optional ``zstd_dict`` payload key, and the chunks list is moved into a
    separately-compressed ``chunks_z`` blob (zstd-with-dict).  Older
    archives written without a dictionary use the original ``chunks`` key,
    and the deserialiser handles both shapes — old files remain readable
    by new code.
    """
    sorted_chunks = sorted(container.chunks.items())
    chunk_values = [data for _, data in sorted_chunks]
    dict_bytes = _train_chunk_dict(chunk_values)
    chunks_blob = _encode_chunks_blob(sorted_chunks)

    payload: dict = {
        "chunk_size": container.chunk_size,
        "files": [{"path": f.path, "sequence": f.sequence} for f in container.files],
    }
    if dict_bytes is not None:
        cdict = zstd.ZstdCompressionDict(dict_bytes)
        payload["zstd_dict"] = dict_bytes
        payload["chunks_blob_z"] = zstd.ZstdCompressor(
            level=_ZSTD_LEVEL, dict_data=cdict, threads=_zstd_threads()
        ).compress(chunks_blob)
    else:
        payload["chunks_blob"] = chunks_blob

    if container.delta_chunks:
        payload["delta_chunks"] = [
            [cid, base_cid, target_len, diffs]
            for cid, (base_cid, target_len, diffs) in sorted(
                container.delta_chunks.items()
            )
        ]
    raw = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL, threads=_zstd_threads())
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
    if "zstd_dict" in payload and "chunks_blob_z" in payload:
        cdict = zstd.ZstdCompressionDict(payload["zstd_dict"])
        chunks_dctx = zstd.ZstdDecompressor(dict_data=cdict)
        try:
            chunks_blob = chunks_dctx.decompress(payload["chunks_blob_z"])
        except zstd.ZstdError as exc:
            raise ValueError(f"Zstandard chunk decompression failed: {exc}") from exc
        chunk_records = _decode_chunks_blob(chunks_blob)
    elif "chunks_blob" in payload:
        chunk_records = _decode_chunks_blob(bytes(payload["chunks_blob"]))
    else:
        chunk_records = payload["chunks"]
    chunks: Dict[int, bytes] = {
        cid: bytes(chunk_bytes) for cid, chunk_bytes in chunk_records
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
