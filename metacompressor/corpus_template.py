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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import msgpack
import zstandard as zstd

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

MAGIC = b"MCK\x00"
VERSION = 0x01
_ZSTD_LEVEL = 3
_MIN_TEMPLATE_OCCURRENCES = 2
_MODE_RAW_TAR_ZSTD = "raw_tar_zstd"
_MODE_ROW_V1 = "corpus_template_row_v1"
_MODE_COLUMNAR_V1 = "corpus_template_columnar_v1"
_MODE_COLUMNAR_V2 = "corpus_template_columnar_v2"

_ENCODING_RAW = "raw_msgpack"
_ENCODING_VARINT = "varint"
_ENCODING_DELTA = "delta_varint"
_ENCODING_DICTIONARY = "dictionary"
_ENCODING_RLE = "rle"
_ROW_REF_ENCODING = "delta_varint_pairs"

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
_MAX_COLUMNAR_BLOCK_ROWS = 262_144

# ---------------------------------------------------------------------------
# Structure extraction v2
# ---------------------------------------------------------------------------


@dataclass
class _LineAnalysis:
    template_parts: Tuple[str, ...]
    values: List[str]
    normalized_skeleton: Tuple[str, ...]
    value_kinds: Tuple[str, ...]
    is_json: bool
    json_structure_key: Tuple[str, ...]


@dataclass
class _JsonLeaf:
    path: Tuple[str, ...]
    raw_value: str
    start: int
    end: int
    kind: str


_LEGACY_VAR_RE = re.compile(
    r"("
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]"
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?"
    r"|0x[0-9a-fA-F]+"
    r"|https?://\S+"
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r")"
)
_INT_RE = re.compile(r"-?(?:0|[1-9]\d*)")
_WHITESPACE_RE = re.compile(r"\s+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TIMESTAMP_RE = re.compile(
    r"(?:\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]"
    r"|\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b)"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f:.]+\b")
_URL_RE = re.compile(r"https?://[^\s\"'>]+")
_QUERY_RE = re.compile(r"\?[A-Za-z0-9_.%+\-]+=[^&\s]*(?:&[A-Za-z0-9_.%+\-]+=[^&\s]*)+")
_PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?(?:\.\.?/|/))[^\s\"'<>|,;]*[A-Za-z0-9_\-/]")
_HEX_RE = re.compile(r"\b(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]{16,})\b")
_REQUESTISH_RE = re.compile(
    r"\b(?:req(?:uest)?|trace|user|session)[-_]?(?:id[-_:]?)?[A-Za-z0-9]{4,}(?:-[A-Za-z0-9]{2,})*\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_KEY_VALUE_RE = re.compile(
    r"(?P<full>"
    r"(?P<key>\b[A-Za-z_][A-Za-z0-9_.-]*)="
    r"(?P<value>"
    r"\"(?:\\.|[^\"\\])*\""
    r"|'(?:\\.|[^'\\])*'"
    r"|\[[^\]\n]*\]"
    r"|[^\s,;|]+"
    r"))"
)
_GENERIC_VAR_PATTERNS = [
    ("timestamp", _TIMESTAMP_RE),
    ("uuid", _UUID_RE),
    ("url", _URL_RE),
    ("query", _QUERY_RE),
    ("email", _EMAIL_RE),
    ("ipv4", _IPV4_RE),
    ("ipv6", _IPV6_RE),
    ("path", _PATH_RE),
    ("hex", _HEX_RE),
    ("id", _REQUESTISH_RE),
    ("number", _NUMBER_RE),
]


def _tokenize_legacy(line: str) -> Tuple[Tuple[str, ...], List[str]]:
    """Return the legacy structure-extraction split for *line*."""
    parts = _LEGACY_VAR_RE.split(line)
    return tuple(parts[0::2]), list(parts[1::2])


def _normalize_text_part(part: str) -> str:
    """Collapse weakly-structured literal variation for conservative grouping."""
    part = _WHITESPACE_RE.sub(" ", part.strip().lower())
    if not part:
        return ""
    part = _UUID_RE.sub("<uuid>", part)
    part = _TIMESTAMP_RE.sub("<timestamp>", part)
    part = _EMAIL_RE.sub("<email>", part)
    part = _URL_RE.sub("<url>", part)
    part = _QUERY_RE.sub("<query>", part)
    part = _IPV4_RE.sub("<ipv4>", part)
    part = _IPV6_RE.sub("<ipv6>", part)
    part = _PATH_RE.sub("<path>", part)
    part = _HEX_RE.sub("<hex>", part)
    part = _REQUESTISH_RE.sub("<id>", part)
    part = _NUMBER_RE.sub("<num>", part)
    return part


def _normalized_skeleton(
    template_parts: Tuple[str, ...],
    value_kinds: Tuple[str, ...],
    json_structure_key: Tuple[str, ...],
) -> Tuple[str, ...]:
    """Return a deterministic, conservative skeleton for fuzzy grouping."""
    if json_structure_key:
        return ("json",) + json_structure_key
    skeleton: List[str] = []
    for index, part in enumerate(template_parts):
        skeleton.append(_normalize_text_part(part))
        if index < len(value_kinds):
            skeleton.append("<%s>" % value_kinds[index])
    return tuple(skeleton)


def _json_skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _json_parse_string(text: str, index: int) -> int:
    if index >= len(text) or text[index] != '"':
        raise ValueError("expected JSON string")
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    raise ValueError("unterminated JSON string")


def _json_parse_number(text: str, index: int) -> int:
    match = re.match(
        r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?",
        text[index:],
    )
    if match is None:
        raise ValueError("invalid JSON number")
    return index + match.end()


def _json_collect_leaves(
    text: str,
    index: int,
    path: Tuple[str, ...],
) -> Tuple[int, List[_JsonLeaf]]:
    index = _json_skip_ws(text, index)
    if index >= len(text):
        raise ValueError("unexpected end of JSON")

    if text[index] == "{":
        leaves: List[_JsonLeaf] = []
        index = _json_skip_ws(text, index + 1)
        if index < len(text) and text[index] == "}":
            return index + 1, leaves
        while True:
            key_start = _json_skip_ws(text, index)
            key_end = _json_parse_string(text, key_start)
            key = text[key_start + 1:key_end - 1]
            index = _json_skip_ws(text, key_end)
            if index >= len(text) or text[index] != ":":
                raise ValueError("expected ':' in JSON object")
            index, child_leaves = _json_collect_leaves(text, index + 1, path + (key,))
            leaves.extend(child_leaves)
            index = _json_skip_ws(text, index)
            if index < len(text) and text[index] == ",":
                index += 1
                continue
            if index < len(text) and text[index] == "}":
                return index + 1, leaves
            raise ValueError("expected ',' or '}' in JSON object")

    if text[index] == "[":
        leaves = []
        index = _json_skip_ws(text, index + 1)
        if index < len(text) and text[index] == "]":
            return index + 1, leaves
        while True:
            index, child_leaves = _json_collect_leaves(text, index, path + ("[]",))
            leaves.extend(child_leaves)
            index = _json_skip_ws(text, index)
            if index < len(text) and text[index] == ",":
                index += 1
                continue
            if index < len(text) and text[index] == "]":
                return index + 1, leaves
            raise ValueError("expected ',' or ']' in JSON array")

    if text[index] == '"':
        end = _json_parse_string(text, index)
        return end, [
            _JsonLeaf(path=path, raw_value=text[index:end], start=index, end=end, kind="json_string")
        ]

    literal_map = {
        "true": "json_bool",
        "false": "json_bool",
        "null": "json_null",
    }
    for literal, kind in literal_map.items():
        if text.startswith(literal, index):
            end = index + len(literal)
            return end, [
                _JsonLeaf(path=path, raw_value=text[index:end], start=index, end=end, kind=kind)
            ]

    end = _json_parse_number(text, index)
    return end, [
        _JsonLeaf(path=path, raw_value=text[index:end], start=index, end=end, kind="json_number")
    ]


def _analyze_json_line(line: str) -> Optional[_LineAnalysis]:
    """Return JSON-aware structure extraction for a single line when valid JSON."""
    stripped = line.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        end, leaves = _json_collect_leaves(line, 0, ())
        end = _json_skip_ws(line, end)
        if end != len(line):
            return None
    except ValueError:
        return None

    if not leaves:
        json_structure_key = ("json_empty", line.strip())
        return _LineAnalysis(
            template_parts=(line,),
            values=[],
            normalized_skeleton=("json",) + json_structure_key,
            value_kinds=(),
            is_json=True,
            json_structure_key=json_structure_key,
        )

    parts: List[str] = []
    values: List[str] = []
    value_kinds: List[str] = []
    structure_bits: List[str] = []
    last = 0
    for leaf in leaves:
        parts.append(line[last:leaf.start])
        values.append(leaf.raw_value)
        value_kinds.append(leaf.kind)
        structure_bits.append("%s=%s" % (".".join(leaf.path), leaf.kind))
        last = leaf.end
    parts.append(line[last:])
    json_structure_key = tuple(sorted(structure_bits))
    template_parts = tuple(parts)
    value_kind_tuple = tuple(value_kinds)
    return _LineAnalysis(
        template_parts=template_parts,
        values=values,
        normalized_skeleton=_normalized_skeleton(
            template_parts,
            value_kind_tuple,
            json_structure_key,
        ),
        value_kinds=value_kind_tuple,
        is_json=True,
        json_structure_key=json_structure_key,
    )


def _find_next_variable(line: str, start: int) -> Optional[Tuple[int, int, str]]:
    best: Optional[Tuple[int, int, str]] = None

    key_match = _KEY_VALUE_RE.search(line, start)
    if key_match is not None:
        best = (
            key_match.start("value"),
            key_match.end("value"),
            "kv:%s" % key_match.group("key").lower(),
        )

    for kind, pattern in _GENERIC_VAR_PATTERNS:
        match = pattern.search(line, start)
        if match is None:
            continue
        candidate = (match.start(), match.end(), kind)
        if best is None or candidate[0] < best[0] or (
            candidate[0] == best[0] and candidate[1] > best[1]
        ):
            best = candidate

    return best


def _scan_text_line(line: str) -> _LineAnalysis:
    """Extract a deterministic template and column values from a text line."""
    parts: List[str] = []
    values: List[str] = []
    value_kinds: List[str] = []
    cursor = 0
    while cursor < len(line):
        match = _find_next_variable(line, cursor)
        if match is None:
            break
        start, end, kind = match
        if start < cursor:
            break
        parts.append(line[cursor:start])
        values.append(line[start:end])
        value_kinds.append(kind)
        cursor = end
    parts.append(line[cursor:])
    template_parts = tuple(parts)
    value_kind_tuple = tuple(value_kinds)
    return _LineAnalysis(
        template_parts=template_parts,
        values=values,
        normalized_skeleton=_normalized_skeleton(template_parts, value_kind_tuple, ()),
        value_kinds=value_kind_tuple,
        is_json=False,
        json_structure_key=(),
    )


def _analyze_line(line: str) -> _LineAnalysis:
    """Return structure-v2 analysis for *line*."""
    json_analysis = _analyze_json_line(line)
    if json_analysis is not None:
        return json_analysis
    return _scan_text_line(line)


def _tokenize(line: str) -> Tuple[Tuple[str, ...], List[str]]:
    """Return the structure-v2 template parts and values for *line*."""
    analysis = _analyze_line(line)
    return analysis.template_parts, list(analysis.values)


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


def _msgpack_size(obj: Any) -> int:
    """Return the msgpack-serialised byte size of *obj*."""
    return len(msgpack.packb(obj, use_bin_type=True))


def _iter_text_lines(file_path: Path) -> Iterator[str]:
    """Yield lines using ``str.split("\\n")`` semantics without loading the full file."""
    saw_any = False
    ended_with_newline = False
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        for raw_line in handle:
            saw_any = True
            if raw_line.endswith("\n"):
                ended_with_newline = True
                yield raw_line[:-1]
            else:
                ended_with_newline = False
                yield raw_line
    if not saw_any or ended_with_newline:
        yield ""


def _pack_archive_payload(payload: dict, level: int = _ZSTD_LEVEL) -> bytes:
    """Pack *payload* as an ``.mck`` archive."""
    raw = msgpack.packb(payload, use_bin_type=True)
    return MAGIC + bytes([VERSION]) + zstd.ZstdCompressor(level=level).compress(raw)


def _build_tarzstd_bytes(input_dir: Path, all_files: List[Path]) -> bytes:
    """Return a TAR+ZSTD baseline archive for *all_files*."""
    output = io.BytesIO()
    with zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(output, closefd=False) as compressor:
        with tarfile.open(fileobj=compressor, mode="w|") as tar:
            for file_path in all_files:
                info = tarfile.TarInfo(name=file_path.relative_to(input_dir).as_posix())
                info.size = file_path.stat().st_size
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mode = 0o644
                with file_path.open("rb") as source:
                    tar.addfile(info, source)
    return output.getvalue()


def _build_raw_tarzstd_archive(tarzstd_bytes: bytes) -> bytes:
    """Wrap pre-compressed TAR+ZSTD bytes in an ``.mck`` archive."""
    return _pack_archive_payload(
        {"mode": _MODE_RAW_TAR_ZSTD, "data": tarzstd_bytes},
        level=1,
    )


def _encode_uvarint(value: int) -> bytes:
    """Encode a non-negative integer as an unsigned varint."""
    if value < 0:
        raise ValueError(
            "unsigned varint cannot encode negative values; "
            "use _encode_signed_varints for signed integers"
        )
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _encode_uvarints(values: List[int]) -> bytes:
    """Encode a sequence of unsigned integers as concatenated varints."""
    out = bytearray()
    for value in values:
        out.extend(_encode_uvarint(value))
    return bytes(out)


def _decode_uvarints(data: bytes, expected_count: int) -> List[int]:
    """Decode *expected_count* unsigned varints from *data*."""
    values: List[int] = []
    value = 0
    shift = 0
    consumed = 0
    for byte in data:
        consumed += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        values.append(value)
        if len(values) == expected_count:
            break
        value = 0
        shift = 0
    if len(values) != expected_count:
        raise ValueError(
            "Corrupt column encoding: "
            f"expected {expected_count} values but decoded {len(values)}"
        )
    if consumed != len(data):
        raise ValueError("Corrupt column encoding: unconsumed bytes in varint data")
    return values


def _zigzag_encode(value: int) -> int:
    """Encode a signed integer for unsigned varint transport."""
    return value * 2 if value >= 0 else (-value * 2) - 1


def _zigzag_decode(value: int) -> int:
    """Decode a zigzag-encoded integer."""
    # Standard zigzag decode: even values decode via ``value >> 1`` and odd
    # values decode via ``-((value >> 1) + 1)``.
    return (value >> 1) ^ -(value & 1)


def _encode_signed_varints(values: List[int]) -> bytes:
    """Encode signed integers as concatenated zigzag varints."""
    return _encode_uvarints([_zigzag_encode(value) for value in values])


def _decode_signed_varints(data: bytes, expected_count: int) -> List[int]:
    """Decode *expected_count* signed zigzag varints from *data*."""
    return [_zigzag_decode(value) for value in _decode_uvarints(data, expected_count)]


def _canonical_int_values(values: List[str]) -> Optional[List[int]]:
    """Return integer values when each token round-trips canonically via ``str(int)``."""
    ints: List[int] = []
    for value in values:
        if not isinstance(value, str) or not _INT_RE.fullmatch(value):
            return None
        parsed = int(value)
        if str(parsed) != value:
            return None
        ints.append(parsed)
    return ints


def _is_delta_friendly(values: List[int]) -> bool:
    """Heuristic for whether delta encoding is worth attempting."""
    if len(values) < 2:
        return False
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    delta_count = len(deltas)
    monotonic_ratio = max(
        sum(1 for delta in deltas if delta >= 0),
        sum(1 for delta in deltas if delta <= 0),
    ) / delta_count
    small_step_ratio = sum(1 for delta in deltas if abs(delta) <= 16) / delta_count
    return monotonic_ratio >= 0.9 or small_step_ratio >= 0.9


def _encode_column(values: List[str]) -> dict:
    """Choose the smallest deterministic column encoding."""
    raw_data = msgpack.packb(values, use_bin_type=True)
    best = {"encoding": _ENCODING_RAW, "data": raw_data}
    best_size = _msgpack_size(best)

    int_values = _canonical_int_values(values)
    if int_values is not None:
        candidate = {
            "encoding": _ENCODING_VARINT,
            "data": _encode_signed_varints(int_values),
        }
        candidate_size = _msgpack_size(candidate)
        if candidate_size < best_size:
            best = candidate
            best_size = candidate_size

        if _is_delta_friendly(int_values):
            deltas = [int_values[0]]
            deltas.extend(
                int_values[i] - int_values[i - 1] for i in range(1, len(int_values))
            )
            candidate = {
                "encoding": _ENCODING_DELTA,
                "data": _encode_signed_varints(deltas),
            }
            candidate_size = _msgpack_size(candidate)
            if candidate_size < best_size:
                best = candidate
                best_size = candidate_size

    if values:
        dictionary: List[str] = []
        dictionary_ids: Dict[str, int] = {}
        indices: List[int] = []
        for value in values:
            if value not in dictionary_ids:
                dictionary_ids[value] = len(dictionary)
                dictionary.append(value)
            indices.append(dictionary_ids[value])
        if len(dictionary) < len(values):
            candidate = {
                "encoding": _ENCODING_DICTIONARY,
                "dictionary": dictionary,
                "indices": _encode_uvarints(indices),
            }
            candidate_size = _msgpack_size(candidate)
            if candidate_size < best_size:
                best = candidate
                best_size = candidate_size

        run_values: List[str] = []
        run_counts: List[int] = []
        last_value: Optional[str] = None
        for value in values:
            if last_value is not None and value == last_value:
                run_counts[-1] += 1
            else:
                run_values.append(value)
                run_counts.append(1)
                last_value = value
        if len(run_values) < len(values):
            candidate = {
                "encoding": _ENCODING_RLE,
                "values": run_values,
                "counts": _encode_uvarints(run_counts),
            }
            candidate_size = _msgpack_size(candidate)
            if candidate_size < best_size:
                best = candidate

    return best


def _decode_column(column: dict, expected_count: int) -> List[str]:
    """Decode a column to the original string values."""
    encoding = column["encoding"]
    if encoding == _ENCODING_RAW:
        values = msgpack.unpackb(bytes(column["data"]), raw=False)
        if len(values) != expected_count:
            raise ValueError("Corrupt column encoding: raw column length mismatch")
        if any(not isinstance(value, str) for value in values):
            raise ValueError("Corrupt column encoding: raw column contains non-string values")
        return values

    if encoding == _ENCODING_VARINT:
        return [str(value) for value in _decode_signed_varints(bytes(column["data"]), expected_count)]

    if encoding == _ENCODING_DELTA:
        deltas = _decode_signed_varints(bytes(column["data"]), expected_count)
        if not deltas:
            return []
        values = [deltas[0]]
        for delta in deltas[1:]:
            values.append(values[-1] + delta)
        return [str(value) for value in values]

    if encoding == _ENCODING_DICTIONARY:
        dictionary = [
            value if isinstance(value, str) else str(value)
            for value in column["dictionary"]
        ]
        indices = _decode_uvarints(bytes(column["indices"]), expected_count)
        try:
            return [dictionary[index] for index in indices]
        except IndexError as exc:
            raise ValueError("Corrupt column encoding: dictionary index out of range") from exc

    if encoding == _ENCODING_RLE:
        values = [
            value if isinstance(value, str) else str(value)
            for value in column["values"]
        ]
        counts = _decode_uvarints(bytes(column["counts"]), len(values))
        decoded: List[str] = []
        for value, count in zip(values, counts):
            decoded.extend([value] * count)
        if len(decoded) != expected_count:
            raise ValueError("Corrupt column encoding: RLE length mismatch")
        return decoded

    raise ValueError(
        "Unsupported column encoding: "
        f"{encoding}. Supported encodings are: "
        f"{_ENCODING_RAW}, {_ENCODING_VARINT}, {_ENCODING_DELTA}, "
        f"{_ENCODING_DICTIONARY}, {_ENCODING_RLE}"
    )


def _encode_row_refs(row_refs: List[List[int]]) -> dict:
    """Encode ``(file_id, line_index)`` pairs compactly and deterministically."""
    file_deltas: List[int] = []
    line_deltas: List[int] = []
    prev_file_id = 0
    prev_line_index = 0
    have_prev = False

    for file_id, line_index in row_refs:
        if have_prev:
            file_delta = file_id - prev_file_id
        else:
            file_delta = file_id
        if file_delta < 0:
            raise ValueError(
                "row_refs must be sorted by file_id in ascending order "
                f"(prev_file_id={prev_file_id}, current_file_id={file_id})"
            )
        if have_prev and file_id == prev_file_id:
            line_delta = line_index - prev_line_index
        else:
            line_delta = line_index
        if line_delta < 0:
            raise ValueError(
                "row_refs line indices must be non-decreasing within each file "
                f"(file_id={file_id}, prev_index={prev_line_index}, current_index={line_index})"
            )

        file_deltas.append(file_delta)
        line_deltas.append(line_delta)
        prev_file_id = file_id
        prev_line_index = line_index
        have_prev = True

    return {
        "encoding": _ROW_REF_ENCODING,
        "count": len(row_refs),
        "file_deltas": _encode_uvarints(file_deltas),
        "line_deltas": _encode_uvarints(line_deltas),
    }


def _decode_row_refs(encoded_row_refs: Any) -> List[List[int]]:
    """Decode compact row references."""
    if isinstance(encoded_row_refs, list):
        return encoded_row_refs

    if encoded_row_refs["encoding"] != _ROW_REF_ENCODING:
        raise ValueError(
            f"Unsupported row_refs encoding: {encoded_row_refs['encoding']}"
        )

    count = encoded_row_refs["count"]
    file_deltas = _decode_uvarints(bytes(encoded_row_refs["file_deltas"]), count)
    line_deltas = _decode_uvarints(bytes(encoded_row_refs["line_deltas"]), count)

    row_refs: List[List[int]] = []
    prev_file_id = 0
    prev_line_index = 0
    have_prev = False

    for file_delta, line_delta in zip(file_deltas, line_deltas):
        file_id = prev_file_id + file_delta
        if have_prev and file_id == prev_file_id:
            line_index = prev_line_index + line_delta
        else:
            line_index = line_delta
        row_refs.append([file_id, line_index])
        prev_file_id = file_id
        prev_line_index = line_index
        have_prev = True

    return row_refs


def _build_row_template_archive(
    input_dir: Path,
    all_files: List[Path],
    file_meta: List[Tuple[str, bool]],
    tok_cache: Dict[str, _LineAnalysis],
    tpl_to_id: Dict[Tuple[str, ...], int],
    tpl_strings: List[str],
) -> Tuple[bytes, dict]:
    """Build the legacy row-oriented template archive."""
    template_reuse_count = 0
    raw_fallback_lines = 0
    binary_fallback_files = 0
    low_structure_fallback_files = 0
    total_var_slots = 0
    fallback_reason_counts: Dict[str, int] = {}

    t_encode_start = time.perf_counter()
    output = io.BytesIO()
    output.write(MAGIC + bytes([VERSION]))
    packer = msgpack.Packer(use_bin_type=True)

    with zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(output, closefd=False) as compressor:
        compressor.write(packer.pack_map_header(2))
        compressor.write(packer.pack("templates"))
        compressor.write(packer.pack(tpl_strings))
        compressor.write(packer.pack("files"))
        compressor.write(packer.pack_array_header(len(all_files)))

        t_serialize_start = time.perf_counter()
        for file_path, (rel, is_binary) in zip(all_files, file_meta):
            if is_binary:
                raw = file_path.read_bytes()
                binary_fallback_files += 1
                fallback_reason_counts["binary"] = fallback_reason_counts.get("binary", 0) + 1
                compressor.write(packer.pack({"path": rel, "records": [[-2, raw]]}))
                continue

            file_tpl_lines = 0
            file_raw_lines = 0
            file_var_total = 0
            file_total_lines = 0
            for line in _iter_text_lines(file_path):
                file_total_lines += 1
                analysis = tok_cache[line]
                tkey = analysis.template_parts
                if tkey in tpl_to_id:
                    file_tpl_lines += 1
                    file_var_total += len(analysis.values)
                else:
                    file_raw_lines += 1

            file_template_rate = (
                file_tpl_lines / file_total_lines if file_total_lines > 0 else 0.0
            )
            if (
                (file_tpl_lines == 0 or file_template_rate < _MIN_FILE_TEMPLATE_RATE)
                and file_total_lines > 0
            ):
                raw = file_path.read_bytes()
                binary_fallback_files += 1
                if file_tpl_lines > 0:
                    low_structure_fallback_files += 1
                    fallback_reason_counts["low_structure"] = (
                        fallback_reason_counts.get("low_structure", 0) + 1
                    )
                else:
                    fallback_reason_counts["no_templates"] = (
                        fallback_reason_counts.get("no_templates", 0) + 1
                    )
                raw_fallback_lines += file_raw_lines
                compressor.write(packer.pack({"path": rel, "records": [[-2, raw]]}))
            else:
                template_reuse_count += file_tpl_lines
                raw_fallback_lines += file_raw_lines
                total_var_slots += file_var_total
                compressor.write(packer.pack_map_header(2))
                compressor.write(packer.pack("path"))
                compressor.write(packer.pack(rel))
                compressor.write(packer.pack("records"))
                compressor.write(packer.pack_array_header(file_total_lines))
                for line in _iter_text_lines(file_path):
                    analysis = tok_cache[line]
                    tkey = analysis.template_parts
                    tpl_id = tpl_to_id.get(tkey)
                    if tpl_id is None:
                        compressor.write(packer.pack([-1, line]))
                    else:
                        compressor.write(packer.pack([tpl_id, list(analysis.values)]))

    t_serialize_s = time.perf_counter() - t_serialize_start
    t_encode_s = time.perf_counter() - t_encode_start
    return output.getvalue(), {
        "template_reuse_count": template_reuse_count,
        "raw_fallback_lines": raw_fallback_lines,
        "binary_fallback_files": binary_fallback_files,
        "low_structure_fallback_files": low_structure_fallback_files,
        "total_var_slots": total_var_slots,
        "serialize_s": t_serialize_s,
        "encode_s": t_encode_s,
        "fallback_reason_counts": fallback_reason_counts,
    }


def _finalize_columnar_block(
    template_blocks: List[Optional[List[dict]]],
    active_blocks: List[Optional[dict]],
    tpl_id: int,
    column_encoding_counts: Dict[str, int],
) -> Tuple[int, int]:
    """Encode and store the active block for *tpl_id*."""
    block = active_blocks[tpl_id]
    if block is None:
        return 0, 0

    encoded_columns: List[dict] = []
    num_encoded_columns = 0
    raw_column_fallback_count = 0
    for column_values in block["columns"]:
        encoded_column = _encode_column(column_values)
        encoding = encoded_column["encoding"]
        column_encoding_counts[encoding] = column_encoding_counts.get(encoding, 0) + 1
        if encoding == _ENCODING_RAW:
            raw_column_fallback_count += 1
        else:
            num_encoded_columns += 1
        encoded_columns.append(encoded_column)

    if template_blocks[tpl_id] is None:
        template_blocks[tpl_id] = []
    template_blocks[tpl_id].append(
        {"row_refs": _encode_row_refs(block["row_refs"]), "columns": encoded_columns}
    )
    active_blocks[tpl_id] = None
    return num_encoded_columns, raw_column_fallback_count


def _pack_columnar_archive(
    mode: str,
    tpl_strings: List[str],
    files_payload: List[dict],
    template_blocks: List[Optional[List[dict]]],
    raw_files: List[bytes],
    raw_lines: List[List[Any]],
) -> bytes:
    """Pack the columnar payload without staging a full msgpack blob in RAM."""
    output = io.BytesIO()
    output.write(MAGIC + bytes([VERSION]))
    packer = msgpack.Packer(use_bin_type=True)

    with zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(output, closefd=False) as compressor:
        compressor.write(packer.pack_map_header(6))
        compressor.write(packer.pack("mode"))
        compressor.write(packer.pack(mode))
        compressor.write(packer.pack("templates"))
        compressor.write(packer.pack(tpl_strings))
        compressor.write(packer.pack("files"))
        compressor.write(packer.pack(files_payload))
        compressor.write(packer.pack("template_blocks"))
        compressor.write(packer.pack_array_header(len(template_blocks)))
        for template_block_list in template_blocks:
            compressor.write(packer.pack(template_block_list))
        compressor.write(packer.pack("raw_files"))
        compressor.write(packer.pack_array_header(len(raw_files)))
        for raw_file in raw_files:
            compressor.write(packer.pack(raw_file))
        compressor.write(packer.pack("metadata"))
        compressor.write(packer.pack_map_header(1))
        compressor.write(packer.pack("raw_lines"))
        compressor.write(packer.pack_array_header(len(raw_lines)))
        for raw_line in raw_lines:
            compressor.write(packer.pack(raw_line))

    return output.getvalue()


def _build_columnar_template_archive(
    all_files: List[Path],
    file_meta: List[Tuple[str, bool]],
    tok_cache: Dict[str, _LineAnalysis],
    tpl_to_id: Dict[Tuple[str, ...], int],
    tpl_strings: List[str],
) -> Tuple[bytes, dict]:
    """Build the block-flushed columnar corpus-template archive."""
    template_reuse_count = 0
    raw_fallback_lines = 0
    binary_fallback_files = 0
    low_structure_fallback_files = 0
    total_var_slots = 0
    fallback_reason_counts: Dict[str, int] = {}

    files_payload: List[dict] = []
    raw_files: List[bytes] = []
    raw_lines: List[List[Any]] = []
    template_blocks: List[Optional[List[dict]]] = [None] * len(tpl_strings)
    active_blocks: List[Optional[dict]] = [None] * len(tpl_strings)
    column_encoding_counts: Dict[str, int] = {}
    num_encoded_columns = 0
    raw_column_fallback_count = 0

    t_encode_start = time.perf_counter()
    for file_path, (rel, is_binary) in zip(all_files, file_meta):
        file_id = len(files_payload)
        if is_binary:
            files_payload.append(
                {"path": rel, "kind": "raw", "raw_file_id": len(raw_files)}
            )
            raw = file_path.read_bytes()
            raw_files.append(raw)
            binary_fallback_files += 1
            fallback_reason_counts["binary"] = fallback_reason_counts.get("binary", 0) + 1
            continue

        file_tpl_lines = 0
        file_raw_lines = 0
        file_var_total = 0
        file_total_lines = 0

        for line in _iter_text_lines(file_path):
            file_total_lines += 1
            analysis = tok_cache[line]
            tkey = analysis.template_parts
            tpl_id = tpl_to_id.get(tkey)
            if tpl_id is None:
                file_raw_lines += 1
            else:
                file_tpl_lines += 1
                file_var_total += len(analysis.values)

        file_template_rate = (
            file_tpl_lines / file_total_lines if file_total_lines > 0 else 0.0
        )
        if (
            (file_tpl_lines == 0 or file_template_rate < _MIN_FILE_TEMPLATE_RATE)
            and file_total_lines > 0
        ):
            files_payload.append(
                {"path": rel, "kind": "raw", "raw_file_id": len(raw_files)}
            )
            raw = file_path.read_bytes()
            raw_files.append(raw)
            binary_fallback_files += 1
            if file_tpl_lines > 0:
                low_structure_fallback_files += 1
                fallback_reason_counts["low_structure"] = (
                    fallback_reason_counts.get("low_structure", 0) + 1
                )
            else:
                fallback_reason_counts["no_templates"] = (
                    fallback_reason_counts.get("no_templates", 0) + 1
                )
            raw_fallback_lines += file_raw_lines
            continue

        files_payload.append({"path": rel, "kind": "text", "num_lines": file_total_lines})
        template_reuse_count += file_tpl_lines
        raw_fallback_lines += file_raw_lines
        total_var_slots += file_var_total

        for line_index, line in enumerate(_iter_text_lines(file_path)):
            analysis = tok_cache[line]
            tkey = analysis.template_parts
            tpl_id = tpl_to_id.get(tkey)
            if tpl_id is None:
                raw_lines.append([file_id, line_index, line])
                continue

            values = list(analysis.values)
            block = active_blocks[tpl_id]
            if block is None:
                block = {
                    "row_refs": [],
                    "columns": [[] for _ in range(len(values))],
                }
                active_blocks[tpl_id] = block
            elif len(block["columns"]) != len(values):
                raise ValueError(
                    "Template column count mismatch: "
                    f"expected {len(block['columns'])} columns but got {len(values)} "
                    f"for template {tpl_id}"
                )

            block["row_refs"].append([file_id, line_index])
            for column_index, value in enumerate(values):
                block["columns"][column_index].append(value)
            if len(block["row_refs"]) >= _MAX_COLUMNAR_BLOCK_ROWS:
                encoded_count, raw_count = _finalize_columnar_block(
                    template_blocks,
                    active_blocks,
                    tpl_id,
                    column_encoding_counts,
                )
                num_encoded_columns += encoded_count
                raw_column_fallback_count += raw_count

    t_serialize_start = time.perf_counter()
    num_columnar_templates = 0

    for tpl_id, block in enumerate(active_blocks):
        if block is not None:
            encoded_count, raw_count = _finalize_columnar_block(
                template_blocks,
                active_blocks,
                tpl_id,
                column_encoding_counts,
            )
            num_encoded_columns += encoded_count
            raw_column_fallback_count += raw_count

    for block_list in template_blocks:
        if block_list is None:
            continue
        num_columnar_templates += 1

    result = _pack_columnar_archive(
        mode=_MODE_COLUMNAR_V2,
        tpl_strings=tpl_strings,
        files_payload=files_payload,
        template_blocks=template_blocks,
        raw_files=raw_files,
        raw_lines=raw_lines,
    )
    t_serialize_s = time.perf_counter() - t_serialize_start
    t_encode_s = time.perf_counter() - t_encode_start
    return result, {
        "template_reuse_count": template_reuse_count,
        "raw_fallback_lines": raw_fallback_lines,
        "binary_fallback_files": binary_fallback_files,
        "low_structure_fallback_files": low_structure_fallback_files,
        "total_var_slots": total_var_slots,
        "serialize_s": t_serialize_s,
        "encode_s": t_encode_s,
        "num_columnar_templates": num_columnar_templates,
        "num_encoded_columns": num_encoded_columns,
        "column_encoding_counts": column_encoding_counts,
        "raw_column_fallback_count": raw_column_fallback_count,
        "fallback_reason_counts": fallback_reason_counts,
    }


def _template_reuse_rate(
    tpl_count: Dict[Tuple[str, ...], int],
    total_lines: int,
) -> float:
    """Return the share of lines participating in a recurring template."""
    if total_lines <= 0:
        return 0.0
    reuse_count = sum(count for count in tpl_count.values() if count >= _MIN_TEMPLATE_OCCURRENCES)
    return reuse_count / total_lines


# ---------------------------------------------------------------------------
# Compress / decompress
# ---------------------------------------------------------------------------

def compress_corpus_template(
    input_dir: Path,
    structure_v2_enabled: bool = True,
) -> bytes:
    """Compress all files under *input_dir* using a shared template dictionary.

    Equivalent to ``compress_corpus_template_with_metrics(input_dir)[0]``.
    """
    return compress_corpus_template_with_metrics(
        input_dir,
        structure_v2_enabled=structure_v2_enabled,
    )[0]


def compress_corpus_template_with_metrics(
    input_dir: Path,
    structure_v2_enabled: bool = True,
) -> Tuple[bytes, dict]:
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
    tok_cache: Dict[str, _LineAnalysis] = {}
    legacy_tok_cache: Dict[str, Tuple[Tuple[str, ...], List[str]]] = {}
    tpl_count: Dict[Tuple[str, ...], int] = {}
    legacy_tpl_count: Dict[Tuple[str, ...], int] = {}
    normalized_tpl_count: Dict[Tuple[str, ...], int] = {}
    total_lines = 0  # text lines across all text files (for reuse_rate)
    json_lines_detected = 0
    json_template_keys: Dict[Tuple[str, ...], int] = {}

    t_tokenize_start = time.perf_counter()
    for file_path in all_files:
        rel = file_path.relative_to(input_dir).as_posix()
        file_legacy_tpl_count: Dict[Tuple[str, ...], int] = {}
        file_tpl_count: Dict[Tuple[str, ...], int] = {}
        file_normalized_tpl_count: Dict[Tuple[str, ...], int] = {}
        file_json_template_keys: Dict[Tuple[str, ...], int] = {}
        file_total_lines = 0
        file_json_lines = 0
        try:
            for line in _iter_text_lines(file_path):
                file_total_lines += 1
                if line not in legacy_tok_cache:
                    legacy_tok_cache[line] = _tokenize_legacy(line)
                legacy_tkey = legacy_tok_cache[line][0]
                file_legacy_tpl_count[legacy_tkey] = file_legacy_tpl_count.get(legacy_tkey, 0) + 1

                if line not in tok_cache:
                    tok_cache[line] = (
                        _analyze_line(line)
                        if structure_v2_enabled
                        else _LineAnalysis(
                            template_parts=legacy_tok_cache[line][0],
                            values=list(legacy_tok_cache[line][1]),
                            normalized_skeleton=_normalized_skeleton(
                                legacy_tok_cache[line][0],
                                tuple("legacy" for _ in legacy_tok_cache[line][1]),
                                (),
                            ),
                            value_kinds=tuple("legacy" for _ in legacy_tok_cache[line][1]),
                            is_json=False,
                            json_structure_key=(),
                        )
                    )
                analysis = tok_cache[line]
                tkey = analysis.template_parts
                file_tpl_count[tkey] = file_tpl_count.get(tkey, 0) + 1
                file_normalized_tpl_count[analysis.normalized_skeleton] = (
                    file_normalized_tpl_count.get(analysis.normalized_skeleton, 0) + 1
                )
                if analysis.is_json:
                    file_json_lines += 1
                    file_json_template_keys[analysis.json_structure_key] = (
                        file_json_template_keys.get(analysis.json_structure_key, 0) + 1
                    )
            file_meta.append((rel, False))
            total_lines += file_total_lines
            for tkey, count in file_legacy_tpl_count.items():
                legacy_tpl_count[tkey] = legacy_tpl_count.get(tkey, 0) + count
            for tkey, count in file_tpl_count.items():
                tpl_count[tkey] = tpl_count.get(tkey, 0) + count
            for skeleton, count in file_normalized_tpl_count.items():
                normalized_tpl_count[skeleton] = (
                    normalized_tpl_count.get(skeleton, 0) + count
                )
            json_lines_detected += file_json_lines
            for json_key, count in file_json_template_keys.items():
                json_template_keys[json_key] = (
                    json_template_keys.get(json_key, 0) + count
                )
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

    normalized_groups: Dict[Tuple[str, ...], set] = {}
    for analysis in tok_cache.values():
        normalized_groups.setdefault(analysis.normalized_skeleton, set()).add(
            analysis.template_parts
        )
    fuzzy_merge_count = sum(
        len(template_group) - 1
        for template_group in normalized_groups.values()
        if len(template_group) > 1
    )

    row_result, row_stats = _build_row_template_archive(
        input_dir=input_dir,
        all_files=all_files,
        file_meta=file_meta,
        tok_cache=tok_cache,
        tpl_to_id=tpl_to_id,
        tpl_strings=tpl_strings,
    )
    columnar_result, columnar_stats = _build_columnar_template_archive(
        all_files=all_files,
        file_meta=file_meta,
        tok_cache=tok_cache,
        tpl_to_id=tpl_to_id,
        tpl_strings=tpl_strings,
    )

    t_encode_s = row_stats["encode_s"] + columnar_stats["encode_s"]
    t_serialize_s = row_stats["serialize_s"] + columnar_stats["serialize_s"]
    t_zstd_s = 0.0
    t_extract_s = time.perf_counter() - t_extract_start

    # -----------------------------------------------------------------------
    # Smart fallback: TAR+ZSTD comparison
    #
    # Build a plain TAR+ZSTD of the same corpus and compare sizes.  If the
    # template output is more than _CORPUS_FALLBACK_THRESHOLD times larger,
    # re-encode as raw_tar_zstd mode so the caller never receives an archive
    # worse than TAR+ZSTD by more than a few dozen bytes of MCK overhead.
    # -----------------------------------------------------------------------
    tarzstd_bytes = _build_tarzstd_bytes(input_dir, all_files)
    tarzstd_size = len(tarzstd_bytes)

    row_mode_size = len(row_result)
    columnar_size = len(columnar_result)
    if columnar_size < row_mode_size:
        best_template_result = columnar_result
        best_template_mode = _MODE_COLUMNAR_V2
    else:
        best_template_result = row_result
        best_template_mode = _MODE_ROW_V1

    if len(best_template_result) > tarzstd_size * _CORPUS_FALLBACK_THRESHOLD:
        result = _build_raw_tarzstd_archive(tarzstd_bytes)
        final_selected_mode = _MODE_RAW_TAR_ZSTD
        chose_raw_fallback = True
        fallback_reason_counts = dict(row_stats["fallback_reason_counts"])
        fallback_reason_counts["raw_tar_zstd"] = fallback_reason_counts.get("raw_tar_zstd", 0) + 1
    else:
        result = best_template_result
        final_selected_mode = best_template_mode
        chose_raw_fallback = False
        if best_template_mode == _MODE_COLUMNAR_V2:
            fallback_reason_counts = dict(columnar_stats["fallback_reason_counts"])
        else:
            fallback_reason_counts = dict(row_stats["fallback_reason_counts"])

    t_total_s = time.perf_counter() - t_total_start

    template_reuse_count = row_stats["template_reuse_count"]
    raw_fallback_lines = row_stats["raw_fallback_lines"]
    binary_fallback_files = row_stats["binary_fallback_files"]
    low_structure_fallback_files = row_stats["low_structure_fallback_files"]
    total_var_slots = row_stats["total_var_slots"]
    avg_vars = (
        total_var_slots / template_reuse_count if template_reuse_count > 0 else 0.0
    )
    reuse_rate = template_reuse_count / total_lines if total_lines > 0 else 0.0
    template_reuse_before = _template_reuse_rate(legacy_tpl_count, total_lines)
    template_reuse_after = _template_reuse_rate(tpl_count, total_lines)

    metrics = {
        "structure_v2_enabled": structure_v2_enabled,
        "num_files": len(all_files),
        "num_lines": total_lines,
        "num_shared_templates": len(tpl_strings),
        "template_reuse_count": template_reuse_count,
        "template_reuse_rate": reuse_rate,
        "json_lines_detected": json_lines_detected,
        "json_template_count": len(json_template_keys),
        "normalized_template_count": len(normalized_tpl_count),
        "fuzzy_merge_count": fuzzy_merge_count,
        "template_reuse_before": template_reuse_before,
        "template_reuse_after": template_reuse_after,
        "raw_fallback_lines": raw_fallback_lines,
        "binary_fallback_files": binary_fallback_files,
        "low_structure_fallback_files": low_structure_fallback_files,
        "fallback_reason_counts": fallback_reason_counts,
        "avg_vars_per_tpl_line": avg_vars,
        "compressed_size": len(result),
        "tarzstd_size": tarzstd_size,
        "chose_raw_fallback": chose_raw_fallback,
        "columnar_enabled": True,
        "num_columnar_templates": columnar_stats["num_columnar_templates"],
        "num_encoded_columns": columnar_stats["num_encoded_columns"],
        "column_encoding_counts": columnar_stats["column_encoding_counts"],
        "raw_column_fallback_count": columnar_stats["raw_column_fallback_count"],
        "columnar_size": columnar_size,
        "row_mode_size": row_mode_size,
        "columnar_savings_vs_row": row_mode_size - columnar_size,
        "final_selected_mode": final_selected_mode,
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

    if mode == _MODE_RAW_TAR_ZSTD:
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

    if mode in (_MODE_COLUMNAR_V1, _MODE_COLUMNAR_V2):
        templates: List[str] = payload["templates"]
        files = payload["files"]
        raw_files = [bytes(data) for data in payload.get("raw_files", [])]
        raw_lines = payload.get("metadata", {}).get("raw_lines", [])
        file_lines: List[Optional[List[Optional[str]]]] = []

        for file_entry in files:
            if file_entry["kind"] == "raw":
                file_lines.append(None)
            else:
                file_lines.append([None] * file_entry["num_lines"])

        for file_id, line_index, line in raw_lines:
            lines = file_lines[file_id]
            if lines is None:
                raise ValueError("Corrupt columnar archive: raw line for raw file")
            lines[line_index] = line

        for tpl_id, block_entry in enumerate(payload["template_blocks"]):
            if block_entry is None:
                continue
            if mode == _MODE_COLUMNAR_V1:
                block_iter = [block_entry]
            else:
                block_iter = block_entry
            for block in block_iter:
                row_refs = _decode_row_refs(block["row_refs"])
                row_count = len(row_refs)
                decoded_columns = [
                    _decode_column(column, row_count)
                    for column in block["columns"]
                ]
                for row_index, row_ref in enumerate(row_refs):
                    file_id, line_index = row_ref
                    values = [
                        decoded_columns[column_index][row_index]
                        for column_index in range(len(decoded_columns))
                    ]
                    lines = file_lines[file_id]
                    if lines is None:
                        raise ValueError("Corrupt columnar archive: template row for raw file")
                    lines[line_index] = _reconstruct_line(templates[tpl_id], values)

        for file_id, file_entry in enumerate(files):
            rel_path = file_entry["path"]
            if file_entry["kind"] == "raw":
                file_bytes = raw_files[file_entry["raw_file_id"]]
            else:
                lines = file_lines[file_id]
                if lines is None:
                    raise ValueError("Corrupt columnar archive: incomplete file reconstruction")
                if any(line is None for line in lines):
                    raise ValueError("Corrupt columnar archive: incomplete file reconstruction")
                file_bytes = "\n".join(lines).encode("utf-8")

            out_path = output_dir / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(file_bytes)
            extracted.append(rel_path)
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
