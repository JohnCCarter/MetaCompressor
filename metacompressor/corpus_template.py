"""Corpus template mode – shared template dictionary across a file corpus.

Unlike per-file template compression (:mod:`metacompressor.log_template`),
this module builds **one** template dictionary over all files in a directory.
Templates that recur across multiple files are stored once and shared, giving
better compression for corpora of structurally similar text files (log
rotations, daily exports, config variants, etc.).

Binary files are stored verbatim (UTF-8 decoding failure → raw bytes record).

Binary layout (.mck file)
--------------------------
[4 bytes] magic   ``MCK\\x00``
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

Payload (msgpack map)
---------------------
``templates``  : list[str]   – shared template strings, indexed by position
``files``      : list[dict]  – one entry per file, each with:
    ``path``    : str         – relative POSIX path
    ``records`` : list        – encoded lines; each record is one of:
        ``[tpl_id, [val, ...]]``  – template-mode line
        ``[-1, raw_line]``        – verbatim text line (template not reused)
        ``[-2, raw_bytes]``       – binary file stored as raw bytes payload
                                    (entire file content, single record)

Public API
----------
compress_corpus_template(input_dir)           -> bytes
decompress_corpus_template(data, output_dir)  -> list[str]
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import msgpack
import zstandard as zstd

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

MAGIC = b"MCK\x00"
VERSION = 0x01
_ZSTD_LEVEL = 3
_MIN_TEMPLATE_OCCURRENCES = 2

# ---------------------------------------------------------------------------
# Tokenisation (mirrors log_template._tokenize / _template_string)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")


def _tokenize(line: str) -> Tuple[Tuple[str, ...], List[str]]:
    """Split *line* into *(template_key, numeric_values)*."""
    parts = _NUM_RE.split(line)
    text_parts: Tuple[str, ...] = tuple(parts[0::2])
    num_parts: List[str] = list(parts[1::2])
    return text_parts, num_parts


def _template_string(text_parts: Tuple[str, ...]) -> str:
    """Build a human-readable template string from *text_parts*."""
    if len(text_parts) == 1:
        return text_parts[0]
    buf: List[str] = []
    for i, part in enumerate(text_parts):
        buf.append(part)
        if i < len(text_parts) - 1:
            buf.append("{}")
    return "".join(buf)


def _reconstruct_line(template_str: str, values: List[str]) -> str:
    """Reconstruct an original log line from *template_str* and *values*."""
    if not values:
        return template_str
    parts = template_str.split("{}")
    buf: List[str] = [parts[0]]
    for i, val in enumerate(values):
        buf.append(val)
        buf.append(parts[i + 1])
    return "".join(buf)


# ---------------------------------------------------------------------------
# Compress / decompress
# ---------------------------------------------------------------------------

def compress_corpus_template(input_dir: Path) -> bytes:
    """Compress all files under *input_dir* using a shared template dictionary.

    Algorithm
    ---------
    1. Walk all files recursively in deterministic order.
    2. Attempt UTF-8 decode of each file; tag binary files for raw storage.
    3. Tokenise every line of every text file and count template-key occurrences
       **globally** across the entire corpus.
    4. Build a shared template dictionary: every template_key with ≥
       :data:`_MIN_TEMPLATE_OCCURRENCES` global occurrences gets an integer ID.
    5. Encode each file using the shared dictionary:
       - template-mode lines → ``[tpl_id, [val, …]]``
       - non-recurring text lines → ``[-1, raw_line]``
       - binary files → single ``[-2, raw_bytes]`` record
    6. Serialise: ``MAGIC + VERSION + zstd(msgpack(payload))``.

    Parameters
    ----------
    input_dir:
        Root directory to compress.

    Returns
    -------
    bytes
        Serialised ``.mck`` byte string.

    Raises
    ------
    ValueError
        If *input_dir* is not a directory.
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise ValueError(f"Not a directory: {input_dir}")

    all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())

    # --- first pass: decode files and collect all lines --------------------
    # Each entry is (rel_path, lines_or_None, raw_bytes_or_None)
    # lines_or_None is None for binary files.
    file_info: List[Tuple[str, Optional[List[str]], Optional[bytes]]] = []
    for file_path in all_files:
        rel = file_path.relative_to(input_dir).as_posix()
        raw = file_path.read_bytes()
        try:
            text = raw.decode("utf-8")
            lines = text.split("\n")
            file_info.append((rel, lines, None))
        except UnicodeDecodeError:
            file_info.append((rel, None, raw))

    # --- count template-key occurrences across *all* text files -----------
    tpl_count: Dict[Tuple[str, ...], int] = {}
    for _, lines, raw_bytes in file_info:
        if lines is None:
            continue
        for line in lines:
            tkey, _ = _tokenize(line)
            tpl_count[tkey] = tpl_count.get(tkey, 0) + 1

    # --- build shared template dictionary ---------------------------------
    # Maintain first-occurrence order for determinism.
    tpl_to_id: Dict[Tuple[str, ...], int] = {}
    tpl_strings: List[str] = []
    for tkey, cnt in tpl_count.items():
        if cnt >= _MIN_TEMPLATE_OCCURRENCES:
            if tkey not in tpl_to_id:
                tpl_to_id[tkey] = len(tpl_strings)
                tpl_strings.append(_template_string(tkey))

    # --- second pass: encode each file ------------------------------------
    encoded_files: List[dict] = []
    for rel, lines, raw_bytes in file_info:
        if raw_bytes is not None:
            # Binary file: single raw-bytes record.
            encoded_files.append({
                "path": rel,
                "records": [[-2, raw_bytes]],
            })
            continue

        records: List = []
        for line in lines:
            tkey, values = _tokenize(line)
            if tkey in tpl_to_id:
                records.append([tpl_to_id[tkey], values])
            else:
                records.append([-1, line])
        encoded_files.append({"path": rel, "records": records})

    payload = {"templates": tpl_strings, "files": encoded_files}
    raw_payload = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    compressed = cctx.compress(raw_payload)
    return MAGIC + bytes([VERSION]) + compressed


def decompress_corpus_template(data: bytes, output_dir: Path) -> List[str]:
    """Decompress a ``.mck`` archive and recreate the directory tree.

    Parameters
    ----------
    data:
        Serialised ``.mck`` byte string.
    output_dir:
        Directory to write recovered files into.  Created if absent.

    Returns
    -------
    list[str]
        Relative paths of all files extracted (in archive order).

    Raises
    ------
    ValueError
        On invalid magic bytes, unsupported version, or corrupt payload.
    """
    if len(data) < 5:
        raise ValueError("Data too short to be a valid .mck file")
    if data[:4] != MAGIC:
        raise ValueError(f"Invalid magic bytes: {data[:4]!r}")
    version = data[4]
    if version != VERSION:
        raise ValueError(f"Unsupported .mck version: {version}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    dctx = zstd.ZstdDecompressor()
    try:
        raw_payload = dctx.decompress(data[5:])
    except zstd.ZstdError as exc:
        raise ValueError(f"Zstandard decompression failed: {exc}") from exc

    payload = msgpack.unpackb(raw_payload, raw=False)
    templates: List[str] = payload["templates"]
    extracted: List[str] = []

    for file_entry in payload["files"]:
        rel_path: str = file_entry["path"]
        records = file_entry["records"]

        if len(records) == 1 and records[0][0] == -2:
            # Binary file stored as raw bytes.
            file_bytes = bytes(records[0][1])
        else:
            # Text file: reconstruct line by line.
            lines: List[str] = []
            for record in records:
                tid = record[0]
                if tid == -1:
                    lines.append(record[1])
                else:
                    tpl_str = templates[tid]
                    values: List[str] = [str(v) for v in record[1]]
                    lines.append(_reconstruct_line(tpl_str, values))
            file_bytes = "\n".join(lines).encode("utf-8")

        out_path = output_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(file_bytes)
        extracted.append(rel_path)

    return extracted
