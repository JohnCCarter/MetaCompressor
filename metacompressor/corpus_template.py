"""Corpus template mode – shared template dictionary across a file corpus.

Unlike per-file template compression (:mod:`metacompressor.log_template`),
this module builds **one** template dictionary over all files in a directory.
Templates that recur across multiple files are stored once and shared, giving
better compression for corpora of structurally similar text files (log
rotations, daily exports, config variants, etc.).

Binary files are stored verbatim (UTF-8 decoding failure → raw bytes record).
Text files whose lines produce no template-mode records are also stored as raw
bytes (hybrid fallback) so that template overhead never hurts single-file
or low-structure corpora.

Streaming design (two-pass, O(1-file) peak memory)
---------------------------------------------------
Pass 1  Read each file → tokenise + count → discard raw bytes and decoded text
        immediately.  Only the *tok_cache* (unique line → ``(tpl_key, values)``)
        and *tpl_count* (``tpl_key → occurrence count``) survive this pass.
        Peak memory during pass 1: O(largest single file + tok_cache).

Pass 2  Re-read each file → encode using the shared template dict → stream file
        entries one-by-one through a :class:`msgpack.Packer` directly into a
        :class:`zstandard.ZstdCompressor.stream_writer`.  No in-memory
        accumulation of the ``encoded_files`` list.  Peak memory during pass 2:
        O(largest single file + tok_cache + tpl_strings + compressed_output).

Win/loss map
------------
**MC wins** (typically 5–30 % smaller than TAR+ZSTD) when:

* The corpus has many files that share the same log template (structured logs,
  metrics, application events).
* ``template_reuse_rate`` ≥ 0.7 — most lines participate in template mode.
* Variable values are short numbers or IDs that compress poorly on their own.
* Many small files: TAR overhead dominates TAR+ZSTD, while MC shares templates
  across all files without per-file overhead.

**MC is comparable or slightly worse** when:

* *Nginx / access logs* – each line has 5–8 variable slots (IP, timestamp,
  path, status, size, latency) filled with high-cardinality unique values.
  The per-record msgpack overhead can rival the structural savings.  When
  template output exceeds TAR+ZSTD by more than ``_CORPUS_FALLBACK_THRESHOLD``,
  the codec falls back transparently to a TAR+ZSTD payload stored inside the
  MCK wrapper (``raw_tar_zstd`` mode), guaranteeing size never exceeds
  TAR+ZSTD + a few dozen bytes.
* *Random or pre-compressed binary data* – all files hit the binary fallback;
  template overhead is zero, but MC cannot beat ZSTD on random data.
* *Prose / natural-language text* – few variable extractions; the
  ``_MIN_FILE_TEMPLATE_RATE`` low-structure fallback stores such files as raw
  bytes, trading per-line msgpack overhead for raw ZSTD compression.

Binary layout (.mck file)
--------------------------
[4 bytes] magic   ``MCK\\x00``
[1 byte]  version  0x01
[N bytes] zstandard-compressed msgpack payload

Payload (msgpack map) — template mode
--------------------------------------
``templates``  : list[str]   – shared template strings, indexed by position
``files``      : list[dict]  – one entry per file, each with:
    ``path``    : str         – relative POSIX path
    ``records`` : list        – encoded lines; each record is one of:
        ``[tpl_id, [val, ...]]``  – template-mode line
        ``[-1, raw_line]``        – verbatim text line (template not reused)
        ``[-2, raw_bytes]``       – binary file stored as raw bytes payload
                                    (entire file content, single record)

Payload (msgpack map) — raw_tar_zstd mode (automatic fallback)
---------------------------------------------------------------
``mode``   : ``"raw_tar_zstd"``
``data``   : bytes  – TAR+ZSTD-compressed corpus (level 3)

This mode is written automatically when the template-mode output would be
more than ``_CORPUS_FALLBACK_THRESHOLD`` × larger than a plain TAR+ZSTD
archive of the same corpus.  Old archives without a ``mode`` key are treated
as template mode (backward-compatible).

Public API
----------
compress_corpus_template(input_dir)                     -> bytes
compress_corpus_template_with_metrics(input_dir)        -> (bytes, dict)
decompress_corpus_template(data, output_dir)            -> list[str]
"""

from __future__ import annotations

import io
import re
import tarfile
import time
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

# Automatic raw fallback: if the template-mode archive is larger than a plain
# TAR+ZSTD of the same corpus by this factor, re-encode in ``raw_tar_zstd``
# mode so the caller never receives an archive bigger than TAR+ZSTD.
# Set to float("inf") to disable the fallback entirely.
_CORPUS_FALLBACK_THRESHOLD = 1.10

# Per-file low-structure fallback: if fewer than this fraction of a text
# file's lines match a recurring template, the whole file is stored as raw
# bytes (same as the 0-template hybrid fallback).  This avoids per-line
# msgpack record overhead for semi-structured files where template reuse is
# sparse.  Set to 0.0 to disable (original behaviour for non-zero cases).
_MIN_FILE_TEMPLATE_RATE = 0.10

# ---------------------------------------------------------------------------
# Tokenisation — extended variable patterns (mirrors log_template._VAR_RE)
# ---------------------------------------------------------------------------

# Extended variable pattern — tried in priority order (most specific first).
# See log_template._VAR_RE for full documentation.
_VAR_RE = re.compile(
    r"("
    # UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    # Nginx/Apache access log timestamp: [DD/Mon/YYYY:HH:MM:SS ±ZZZZ]
    # Captures the entire bracket as one token, avoiding spurious variable slots
    # for the constant day/year/hour/timezone fields common in access logs.
    r"|\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]"
    # ISO 8601 datetime (date+time separator required; timezone optional)
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    # IPv4 address with optional :port (before plain numbers to avoid partial match)
    r"|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?"
    # Hex string with 0x prefix
    r"|0x[0-9a-fA-F]+"
    # URL with http or https scheme
    r"|https?://\S+"
    # Number: signed integer, float, or scientific notation (existing behaviour)
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r")"
)


def _tokenize(line: str) -> Tuple[Tuple[str, ...], List[str]]:
    """Split *line* into *(template_key, values)*.

    Recognised variable types (matched in priority order):
    UUID, ISO-8601 datetime, IPv4(+port), 0x-hex, URL, number.
    """
    parts = _VAR_RE.split(line)
    text_parts: Tuple[str, ...] = tuple(parts[0::2])
    var_parts: List[str] = list(parts[1::2])
    return text_parts, var_parts


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

    Equivalent to ``compress_corpus_template_with_metrics(input_dir)[0]``.
    """
    return compress_corpus_template_with_metrics(input_dir)[0]


def compress_corpus_template_with_metrics(input_dir: Path) -> Tuple[bytes, dict]:
    """Compress all files under *input_dir* using a shared template dictionary.

    Algorithm (two-pass streaming, O(1-file) peak memory)
    ------------------------------------------------------
    **Pass 1** – tokenise + count (one file at a time; raw bytes discarded
    after each file):

    1. Walk all files recursively in deterministic order.
    2. Attempt UTF-8 decode; tag binary files.
    3. Tokenise every line of every text file; populate *tok_cache* (unique
       line → ``(template_key, values)``) and count global template-key
       occurrences.

    **Build shared dictionary** from keys with ≥
    :data:`_MIN_TEMPLATE_OCCURRENCES` occurrences.

    **Pass 2** – encode + stream output (one file at a time; records flushed
    immediately to the zstd stream writer):

    4. Re-read each file, encode using the shared dictionary:
       - template-mode lines → ``[tpl_id, [val, …]]``
       - non-recurring text lines → ``[-1, raw_line]``
       - binary files → single ``[-2, raw_bytes]`` record
       - text files with zero template-mode lines → ``[-2, raw_bytes]`` record
         (hybrid fallback: avoids raw-line overhead for template-poor files)
    5. Each encoded file entry is packed with :class:`msgpack.Packer` and
       written directly to a :class:`zstd.ZstdCompressor.stream_writer`,
       avoiding in-memory accumulation of the full encoded-files list.

    **Smart fallback** – if the template output exceeds a plain TAR+ZSTD
    archive of the same corpus by more than :data:`_CORPUS_FALLBACK_THRESHOLD`,
    re-encode as ``raw_tar_zstd`` mode.  This guarantees that callers never
    receive an archive larger than TAR+ZSTD + a few dozen bytes overhead.

    Parameters
    ----------
    input_dir:
        Root directory to compress.

    Returns
    -------
    tuple[bytes, dict]
        ``(compressed_bytes, metrics)`` where *metrics* is a dict with keys:

        - ``num_files``               – total files processed
        - ``num_lines``               – total text lines across all text files
        - ``num_shared_templates``    – entries in the shared template dict
        - ``template_reuse_count``    – total template-mode line records written
        - ``template_reuse_rate``     – template_reuse_count / num_lines (0–1)
        - ``raw_fallback_lines``      – lines stored verbatim (``[-1, ...]``)
        - ``binary_fallback_files``   – files stored as raw bytes (UTF-8 failure,
                                        hybrid fallback, or low-structure fallback)
        - ``low_structure_fallback_files`` – text files that had some recurring
                                        templates but below the
                                        :data:`_MIN_FILE_TEMPLATE_RATE` threshold;
                                        stored as raw bytes to avoid per-line
                                        msgpack overhead (subset of
                                        ``binary_fallback_files``)
        - ``avg_vars_per_tpl_line``   – average number of variable slots used
                                        across template-mode lines
        - ``compressed_size``         – byte length of the compressed output
        - ``tarzstd_size``            – byte length of equivalent TAR+ZSTD
                                        (computed for the fallback comparison)
        - ``chose_raw_fallback``      – ``True`` when the codec chose
                                        ``raw_tar_zstd`` mode because template
                                        output exceeded the fallback threshold
        - ``timing``                  – sub-timing dict with keys
                                        ``tokenize_s``, ``count_s``,
                                        ``encode_s``, ``extract_s``,
                                        ``serialize_s``, ``zstd_s``, ``total_s``

    Raises
    ------
    ValueError
        If *input_dir* is not a directory.
    """
    t_total_start = time.perf_counter()

    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise ValueError(f"Not a directory: {input_dir}")

    all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())

    # -----------------------------------------------------------------------
    # Pass 1: tokenise + count
    #
    # Read each file, tokenise, count, then discard raw bytes and decoded text.
    # Only tok_cache and tpl_count survive this pass, keeping peak memory at
    # O(largest single file + tok_cache) instead of O(entire_corpus).
    # -----------------------------------------------------------------------
    t_extract_start = time.perf_counter()

    # file_meta stores (rel_path, is_binary) only — no raw bytes between passes.
    file_meta: List[Tuple[str, bool]] = []

    # tok_cache: unique line → (template_key, variable_values).
    # One regex call per *unique* line; for repetitive corpora this reduces
    # O(N) regex splits to O(distinct lines), typically a handful.
    tok_cache: Dict[str, Tuple[Tuple[str, ...], List[str]]] = {}
    tpl_count: Dict[Tuple[str, ...], int] = {}
    total_lines = 0  # text lines across all text files (for reuse_rate)

    t_tokenize_start = time.perf_counter()
    for file_path in all_files:
        rel = file_path.relative_to(input_dir).as_posix()
        raw = file_path.read_bytes()
        try:
            text = raw.decode("utf-8")
            lines = text.split("\n")
            file_meta.append((rel, False))
            for line in lines:
                total_lines += 1
                if line not in tok_cache:
                    tok_cache[line] = _tokenize(line)
                tkey = tok_cache[line][0]
                tpl_count[tkey] = tpl_count.get(tkey, 0) + 1
        except UnicodeDecodeError:
            file_meta.append((rel, True))
        # raw, text, lines are freed at end of each iteration — O(1 file) peak.
    t_tokenize_s = time.perf_counter() - t_tokenize_start
    t_count_s = 0.0  # tokenise and count are combined in a single pass above

    # -----------------------------------------------------------------------
    # Build shared template dictionary
    # -----------------------------------------------------------------------
    tpl_to_id: Dict[Tuple[str, ...], int] = {}
    tpl_strings: List[str] = []
    for tkey, cnt in tpl_count.items():
        if cnt >= _MIN_TEMPLATE_OCCURRENCES:
            if tkey not in tpl_to_id:
                tpl_to_id[tkey] = len(tpl_strings)
                tpl_strings.append(_template_string(tkey))

    # -----------------------------------------------------------------------
    # Pass 2: encode + stream output
    #
    # Re-read each file, encode using tok_cache and tpl_to_id, and write each
    # encoded file entry immediately to a msgpack Packer piped into a zstd
    # stream writer.  No in-memory accumulation of encoded_files list.
    # -----------------------------------------------------------------------
    template_reuse_count = 0
    raw_fallback_lines = 0
    binary_fallback_files = 0
    low_structure_fallback_files = 0
    total_var_slots = 0

    t_encode_start = time.perf_counter()

    output = io.BytesIO()
    output.write(MAGIC + bytes([VERSION]))
    packer = msgpack.Packer(use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)

    with cctx.stream_writer(output, closefd=False) as compressor:
        # Streaming msgpack map: {"templates": [...], "files": [...]}
        # pack_map_header(2) + keys/values produces the same bytes as
        # msgpack.packb({"templates": ..., "files": [...]}) for 2-key maps.
        compressor.write(packer.pack_map_header(2))
        compressor.write(packer.pack("templates"))
        compressor.write(packer.pack(tpl_strings))
        compressor.write(packer.pack("files"))
        compressor.write(packer.pack_array_header(len(all_files)))

        t_serialize_start = time.perf_counter()
        for file_path, (rel, is_binary) in zip(all_files, file_meta):
            raw = file_path.read_bytes()  # re-read for pass 2

            if is_binary:
                binary_fallback_files += 1
                compressor.write(packer.pack({"path": rel, "records": [[-2, raw]]}))
                continue

            text = raw.decode("utf-8")
            lines = text.split("\n")
            records: List = []
            file_tpl_lines = 0
            file_raw_lines = 0
            file_var_total = 0
            for line in lines:
                tkey, values = tok_cache[line]  # always a cache hit
                if tkey in tpl_to_id:
                    records.append([tpl_to_id[tkey], values])
                    file_tpl_lines += 1
                    file_var_total += len(values)
                else:
                    records.append([-1, line])
                    file_raw_lines += 1

            # Hybrid / low-structure fallback: store the file as raw bytes when:
            #   (a) no lines used template mode at all, OR
            #   (b) template usage is sparse (< _MIN_FILE_TEMPLATE_RATE).
            # raw is already available from the re-read above.
            file_total_lines = len(lines)
            file_template_rate = (
                file_tpl_lines / file_total_lines if file_total_lines > 0 else 0.0
            )
            if (
                (file_tpl_lines == 0 or file_template_rate < _MIN_FILE_TEMPLATE_RATE)
                and lines
            ):
                binary_fallback_files += 1
                if file_tpl_lines > 0:
                    low_structure_fallback_files += 1
                raw_fallback_lines += file_raw_lines
                compressor.write(packer.pack({"path": rel, "records": [[-2, raw]]}))
            else:
                template_reuse_count += file_tpl_lines
                raw_fallback_lines += file_raw_lines
                total_var_slots += file_var_total
                compressor.write(packer.pack({"path": rel, "records": records}))

    t_serialize_s = time.perf_counter() - t_serialize_start
    t_zstd_s = 0.0  # interleaved with serialisation inside stream_writer
    t_encode_s = time.perf_counter() - t_encode_start
    t_extract_s = time.perf_counter() - t_extract_start

    template_result = output.getvalue()

    # -----------------------------------------------------------------------
    # Smart fallback: TAR+ZSTD comparison
    #
    # Build a plain TAR+ZSTD of the same corpus and compare sizes.  If the
    # template output is more than _CORPUS_FALLBACK_THRESHOLD times larger,
    # re-encode as raw_tar_zstd mode so the caller never receives an archive
    # worse than TAR+ZSTD by more than a few dozen bytes of MCK overhead.
    # -----------------------------------------------------------------------
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        for fp in all_files:
            tar.add(str(fp), arcname=fp.relative_to(input_dir).as_posix())
    tarzstd_bytes = zstd.ZstdCompressor(level=_ZSTD_LEVEL).compress(tar_buf.getvalue())
    tarzstd_size = len(tarzstd_bytes)
    del tar_buf  # free uncompressed TAR bytes promptly

    if len(template_result) > tarzstd_size * _CORPUS_FALLBACK_THRESHOLD:
        # Template mode is significantly worse — fall back to raw_tar_zstd.
        # The TAR+ZSTD bytes are already compressed; we wrap them in a minimal
        # msgpack dict and re-compress with zstd level 1 (pre-compressed data
        # is incompressible, so level 1 adds only ~30 bytes overhead).
        raw_payload = msgpack.packb(
            {"mode": "raw_tar_zstd", "data": tarzstd_bytes},
            use_bin_type=True,
        )
        result = MAGIC + bytes([VERSION]) + zstd.ZstdCompressor(level=1).compress(raw_payload)
        chose_raw_fallback = True
    else:
        result = template_result
        chose_raw_fallback = False

    t_total_s = time.perf_counter() - t_total_start

    avg_vars = (
        total_var_slots / template_reuse_count if template_reuse_count > 0 else 0.0
    )
    reuse_rate = template_reuse_count / total_lines if total_lines > 0 else 0.0

    metrics = {
        "num_files": len(all_files),
        "num_lines": total_lines,
        "num_shared_templates": len(tpl_strings),
        "template_reuse_count": template_reuse_count,
        "template_reuse_rate": reuse_rate,
        "raw_fallback_lines": raw_fallback_lines,
        "binary_fallback_files": binary_fallback_files,
        "low_structure_fallback_files": low_structure_fallback_files,
        "avg_vars_per_tpl_line": avg_vars,
        "compressed_size": len(result),
        "tarzstd_size": tarzstd_size,
        "chose_raw_fallback": chose_raw_fallback,
        "timing": {
            "tokenize_s": t_tokenize_s,
            "count_s": t_count_s,
            "encode_s": t_encode_s,
            "extract_s": t_extract_s,
            "serialize_s": t_serialize_s,
            "zstd_s": t_zstd_s,
            "total_s": t_total_s,
        },
    }
    return result, metrics


def decompress_corpus_template(data: bytes, output_dir: Path) -> List[str]:
    """Decompress a ``.mck`` archive and recreate the directory tree.

    Supports both *template* mode (default) and *raw_tar_zstd* mode (automatic
    fallback written by the compressor when template output would be larger than
    a plain TAR+ZSTD of the same corpus).

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
        with dctx.stream_reader(io.BytesIO(data[5:])) as reader:
            raw_payload = reader.read()
    except zstd.ZstdError as exc:
        raise ValueError(f"Zstandard decompression failed: {exc}") from exc

    payload = msgpack.unpackb(raw_payload, raw=False)
    extracted: List[str] = []

    mode = payload.get("mode", "template")

    if mode == "raw_tar_zstd":
        # Automatic fallback path: payload contains TAR+ZSTD bytes.
        tarzstd_data = bytes(payload["data"])
        with dctx.stream_reader(io.BytesIO(tarzstd_data)) as reader:
            tar_bytes = reader.read()
        buf = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    out_path = output_dir / member.name
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    f = tar.extractfile(member)
                    if f is not None:
                        out_path.write_bytes(f.read())
                    extracted.append(member.name)
        return extracted

    # Template mode (original path; also used for old archives without "mode" key).
    templates: List[str] = payload["templates"]

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
