"""Short msgpack map keys for .mc1 / .mc1dir (versioned wire optimization).

Writers emit **short** keys; readers accept **long** (legacy) or **short** keys
interchangeably.  Inner ``files`` entries may use ``path`` / ``sequence`` or
``p`` / ``s``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import msgpack

# Top-level keys (v1 short wire)
K_CHUNK_SIZE = "cs"
K_CHUNKS = "c"
K_SEQUENCE = "s"
K_FILES = "f"
K_DELTA_CHUNKS = "dc"
K_CHUNKING_MODE = "cm"
K_MIN_CS = "mnc"
K_AVG_CS = "avc"
K_MAX_CS = "mxc"
K_CDC_MASK = "cdm"


def _get_first(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    raise KeyError(keys[0])


def _norm_file_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    if "path" in entry and "sequence" in entry:
        return entry
    return {
        "path": entry["p"],
        "sequence": list(entry["s"]),
    }


def normalise_mc1_payload_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with canonical long keys (for downstream logic)."""
    out: Dict[str, Any] = {}
    cm = payload.get("chunking_mode", payload.get(K_CHUNKING_MODE, "fixed"))
    out["chunking_mode"] = cm
    out["chunks"] = [
        [int(row[0]), bytes(row[1])] for row in _get_first(payload, "chunks", K_CHUNKS)
    ]
    out["sequence"] = list(_get_first(payload, "sequence", K_SEQUENCE))
    if "delta_chunks" in payload or K_DELTA_CHUNKS in payload:
        out["delta_chunks"] = list(
            payload.get("delta_chunks", payload.get(K_DELTA_CHUNKS, []))
        )
    if cm == "cdc":
        out["min_chunk_size"] = payload.get("min_chunk_size", payload.get(K_MIN_CS))
        out["avg_chunk_size"] = payload.get("avg_chunk_size", payload.get(K_AVG_CS))
        out["max_chunk_size"] = payload.get("max_chunk_size", payload.get(K_MAX_CS))
        out["cdc_mask"] = payload.get("cdc_mask", payload.get(K_CDC_MASK))
    else:
        out["chunk_size"] = int(_get_first(payload, "chunk_size", K_CHUNK_SIZE))
    return out


def normalise_mc1dir_payload_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["chunk_size"] = int(_get_first(payload, "chunk_size", K_CHUNK_SIZE))
    out["chunks"] = [
        [int(row[0]), bytes(row[1])] for row in _get_first(payload, "chunks", K_CHUNKS)
    ]
    raw_files = _get_first(payload, "files", K_FILES)
    out["files"] = [_norm_file_entry(dict(e)) for e in raw_files]
    if "delta_chunks" in payload or K_DELTA_CHUNKS in payload:
        out["delta_chunks"] = list(
            payload.get("delta_chunks", payload.get(K_DELTA_CHUNKS, []))
        )
    return out


def _pack_file_entry_short(packer: msgpack.Packer, entry: Any) -> bytes:
    if isinstance(entry, dict) and "path" in entry:
        p = entry["path"]
        s = entry["sequence"]
    else:
        p = entry.path
        s = entry.sequence
    return (
        packer.pack_map_header(2)
        + packer.pack("p")
        + packer.pack(p)
        + packer.pack("s")
        + packer.pack(s)
    )


def write_mc1_msgpack_short_stream(
    container: Any,
    write: Callable[[bytes], None],
    *,
    single_file: bool,
) -> None:
    """Stream msgpack map (short keys) to *write* without ``packb`` of full tree."""
    packer = msgpack.Packer(use_bin_type=True)
    sorted_chunks = sorted(container.chunks.items())

    if single_file:
        n_map = 3 + (1 if container.delta_chunks else 0)
        if container.chunking_mode == "cdc":
            n_map += 4
        else:
            n_map += 1
        write(packer.pack_map_header(n_map))
        write(packer.pack(K_CHUNKING_MODE))
        write(packer.pack(container.chunking_mode))
        write(packer.pack(K_CHUNKS))
        write(packer.pack_array_header(len(sorted_chunks)))
        for cid, data in sorted_chunks:
            write(packer.pack([cid, data]))
        write(packer.pack(K_SEQUENCE))
        write(packer.pack(container.sequence))
        if container.delta_chunks:
            entries = sorted(container.delta_chunks.items())
            write(packer.pack(K_DELTA_CHUNKS))
            write(packer.pack_array_header(len(entries)))
            for cid, (base_cid, target_len, diffs) in entries:
                write(packer.pack([cid, base_cid, target_len, diffs]))
        if container.chunking_mode == "cdc":
            write(packer.pack(K_MIN_CS))
            write(packer.pack(container.min_chunk_size))
            write(packer.pack(K_AVG_CS))
            write(packer.pack(container.avg_chunk_size))
            write(packer.pack(K_MAX_CS))
            write(packer.pack(container.max_chunk_size))
            write(packer.pack(K_CDC_MASK))
            write(packer.pack(container.cdc_mask))
        else:
            write(packer.pack(K_CHUNK_SIZE))
            write(packer.pack(container.chunk_size))
        return

    # .mc1dir
    n_map = 3 + (1 if container.delta_chunks else 0)
    write(packer.pack_map_header(n_map))
    write(packer.pack(K_CHUNK_SIZE))
    write(packer.pack(container.chunk_size))
    write(packer.pack(K_CHUNKS))
    write(packer.pack_array_header(len(sorted_chunks)))
    for cid, data in sorted_chunks:
        write(packer.pack([cid, data]))
    write(packer.pack(K_FILES))
    write(packer.pack_array_header(len(container.files)))
    for fe in container.files:
        write(_pack_file_entry_short(packer, fe))
    if container.delta_chunks:
        entries = sorted(container.delta_chunks.items())
        write(packer.pack(K_DELTA_CHUNKS))
        write(packer.pack_array_header(len(entries)))
        for cid, (base_cid, target_len, diffs) in entries:
            write(packer.pack([cid, base_cid, target_len, diffs]))


def pack_mc1_payload_msgpack_bytes_short(container: Any) -> bytes:
    """Non-streaming pack (tests / size checks) using short keys."""
    buf = bytearray()
    write_mc1_msgpack_short_stream(container, buf.extend, single_file=True)
    return bytes(buf)


def pack_mc1dir_payload_msgpack_bytes_short(container: Any) -> bytes:
    buf = bytearray()
    write_mc1_msgpack_short_stream(container, buf.extend, single_file=False)
    return bytes(buf)
