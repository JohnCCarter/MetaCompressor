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
  The per-record msgpack overhead can rival the structural savings.  **Adaptive
  selection v1** picks the smallest final ``.mck`` among row mode, columnar
  (v1/v2 encodings), and TAR+ZSTD-in-MCK; row/columnar are only eligible when
  their packed size is at most ``_CORPUS_FALLBACK_THRESHOLD`` × plain TAR+ZSTD
  bytes, while ``raw_tar_zstd`` stays in the pool as a safe fallback.
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
import itertools
import math
import re
import tarfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Pattern, Sequence, Set, Tuple

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
_MODE_PLAIN_TAR_ZSTD_PASSTHROUGH = "plain_tar_zstd_passthrough"
_MODE_ROW_V1 = "corpus_template_row_v1"
_MODE_COLUMNAR_V1 = "corpus_template_columnar_v1"
_MODE_COLUMNAR_V2 = "corpus_template_columnar_v2"
_MODE_HYBRID_ROW_COLUMNAR_V1 = "corpus_template_hybrid_row_columnar_v1"

_ENCODING_RAW = "raw_msgpack"
_ENCODING_VARINT = "varint"
_ENCODING_DELTA = "delta_varint"
_ENCODING_DICTIONARY = "dictionary"
_ENCODING_RLE = "rle"
_ENCODING_PREFIX_SUFFIX_DICTIONARY = "prefix_suffix_dictionary"
_ENCODING_URL_PATH_PREFIX = "url_path_prefix_encoding"
_ENCODING_TIMESTAMP_STRING_DELTA = "timestamp_string_delta"
_ENCODING_STRING_PATTERN_V1 = "string_pattern_encoding_v1"
_ENCODING_CHAINED_COLUMN_V1 = "chained_column_v1"
_ROW_REF_ENCODING = "delta_varint_pairs"

# Column encoding profiles (corpus-template columnar). ``v2`` evaluates dictionary
# and RLE; ``v1`` matches the pre–columnar-v2 strategy (raw + varint + delta only).
_COLUMN_ENCODE_PROFILE_V1 = "v1"
_COLUMN_ENCODE_PROFILE_V2 = "v2"
_COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2 = "field_aware_v2"
_COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1 = "string_pattern_v1"
_COLUMN_ENCODE_PROFILE_PIPELINE_V1 = "pipeline_v1"
_COLUMN_ENCODE_PROFILE_RELATIONAL_V1 = "relational_v1"

_CHAINED_VARIANT_PS_SP = "prefix_suffix_string_pattern"
_CHAINED_VARIANT_URL_SP = "url_path_string_pattern"

_NAIVE_EPOCH = datetime(1970, 1, 1, 0, 0, 0)

_TS_STRING_DELTA_SPECS: List[Tuple[int, str, Pattern[str]]] = [
    (
        0,
        "%Y-%m-%d %H:%M:%S",
        re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
    ),
    (
        1,
        "%Y-%m-%dT%H:%M:%SZ",
        re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
    ),
]

_TS_FMT_BY_ID: Dict[int, str] = {sid: fmt for sid, fmt, _p in _TS_STRING_DELTA_SPECS}

# Automatic raw fallback: if the template-mode archive is larger than a plain
# TAR+ZSTD of the same corpus by this factor, re-encode in ``raw_tar_zstd``
# mode so the caller never receives an archive bigger than TAR+ZSTD.
# Set to float("inf") to disable the fallback entirely.
_CORPUS_FALLBACK_THRESHOLD = 1.10

# Adaptive mode selection v1: candidate keys in ``metrics["candidate_sizes"]``.
_ADAPT_ROW = "row_template"
_ADAPT_COL_V2 = "columnar_encoding_v2"
_ADAPT_COL_V1 = "columnar_encoding_v1"
_ADAPT_HYBRID = "hybrid_row_columnar_v1"
_ADAPT_FIELD_AWARE = "field_aware_columnar_v2"
_ADAPT_STRING_PATTERN = "string_pattern_encoding_v1"
_ADAPT_PIPELINE = "pipeline_columnar_v1"
_ADAPT_RELATIONAL = "relational_encoding_v1"
_ADAPT_TAR = "raw_tar_zstd"

_PROFILE_GENERIC = "generic"
_PROFILE_LOGS = "logs"
_PROFILE_NGINX = "nginx"
_PROFILE_JSON = "json"

_PREDICTIVE_V23_CONFIDENCE_HIGH = 0.060
_PREDICTIVE_V23_CONFIDENCE_MEDIUM = 0.025
_PREDICTIVE_V23_REGRESSION_GUARD = 1.02
_UNIVERSAL_TAR_SIZE_GUARD_EPSILON = 0.02

_STRING_PATTERN_CATALOG: Tuple[str, ...] = (
    "https://",
    "http://",
    "/api/",
    "/v1/",
    "/v2/",
    ".json",
    ".html",
    ".log",
    "GET ",
    "POST ",
    "PUT ",
    "DELETE ",
    "PATCH ",
    "INFO ",
    "WARN ",
    "ERROR ",
    "DEBUG ",
    "user=",
    "item=",
    "status=",
    "path=",
    "trace=",
    "seq=",
    "ts=",
    "route=",
    "region=",
    "latency_",
    "url=",
    "msg=",
    "id=",
)

_STRING_PATTERN_MAX_TOKENS = 48
_STRING_PATTERN_WORD_RE = re.compile(r"[^A-Za-z0-9]+")

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
            key = text[key_start + 1 : key_end - 1]
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
            _JsonLeaf(
                path=path,
                raw_value=text[index:end],
                start=index,
                end=end,
                kind="json_string",
            )
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
                _JsonLeaf(
                    path=path,
                    raw_value=text[index:end],
                    start=index,
                    end=end,
                    kind=kind,
                )
            ]

    end = _json_parse_number(text, index)
    return end, [
        _JsonLeaf(
            path=path,
            raw_value=text[index:end],
            start=index,
            end=end,
            kind="json_number",
        )
    ]


def _line_analysis_from_json_leaves(
    line: str, leaves: List[_JsonLeaf]
) -> _LineAnalysis:
    """Build a :class:`_LineAnalysis` from JSON leaves (indices relative to *line*)."""
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
        parts.append(line[last : leaf.start])
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


def _analyze_json_line(line: str) -> Optional[_LineAnalysis]:
    """Return JSON-aware structure extraction when the line contains a JSON value.

    Handles:

    * Whole-line JSON objects/arrays (possibly with leading/trailing whitespace).
    * **NDJSON / log-prefix lines** where a JSON object or array starts after a
      non-JSON prefix (e.g. ``2026-01-01T00:00:00Z {...}``). The **leftmost**
      ``{``/``[`` that yields a parse consuming the rest of the line (ignoring
      trailing spaces) wins — deterministic and conservative (no AI).
    """
    lead = 0
    while lead < len(line) and line[lead] in " \t\r\n":
        lead += 1
    if lead >= len(line):
        return None

    for start in range(lead, len(line)):
        if line[start] not in "{[":
            continue
        try:
            end, leaves = _json_collect_leaves(line, start, ())
            end = _json_skip_ws(line, end)
            if end != len(line):
                continue
        except ValueError:
            continue

        return _line_analysis_from_json_leaves(line, leaves)

    return None


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
        if (
            best is None
            or candidate[0] < best[0]
            or (candidate[0] == best[0] and candidate[1] > best[1])
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
    with zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(
        output, closefd=False
    ) as compressor:
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


def _adaptive_select_output(
    *,
    tarzstd_bytes: bytes,
    tarzstd_size: int,
    row_result: bytes,
    row_stats: Dict[str, Any],
    columnar_v2_result: bytes,
    columnar_v2_stats: Dict[str, Any],
    columnar_v1_result: bytes,
    columnar_v1_stats: Dict[str, Any],
) -> Tuple[bytes, str, bool, Dict[str, Any], Dict[str, Any]]:
    """Pick the smallest valid final ``.mck`` among row, columnar v2/v1, and TAR+ZSTD.

    Row and columnar candidates are only eligible when their packed size is at
    most ``tarzstd_size * _CORPUS_FALLBACK_THRESHOLD`` (same gate as the legacy
    compressor). The TAR+ZSTD ``.mck`` wrapper is always eligible. Deterministic
    tie-break when sizes tie: row < columnar v2 < columnar v1 < TAR.
    """
    tar_mck = _build_raw_tarzstd_archive(tarzstd_bytes)
    size_tar_mck = len(tar_mck)

    limit = tarzstd_size * _CORPUS_FALLBACK_THRESHOLD
    limitless = math.isinf(limit)

    candidate_sizes: Dict[str, int] = {
        _ADAPT_ROW: len(row_result),
        _ADAPT_COL_V2: len(columnar_v2_result),
        _ADAPT_COL_V1: len(columnar_v1_result),
        _ADAPT_TAR: size_tar_mck,
    }

    rejected: List[Dict[str, Any]] = []
    template_rows: List[Tuple[int, str, str, bytes, Dict[str, Any]]] = [
        (0, _ADAPT_ROW, _MODE_ROW_V1, row_result, row_stats),
        (1, _ADAPT_COL_V2, _MODE_COLUMNAR_V2, columnar_v2_result, columnar_v2_stats),
        (2, _ADAPT_COL_V1, _MODE_COLUMNAR_V2, columnar_v1_result, columnar_v1_stats),
    ]

    pool: List[Tuple[int, int, str, str, bytes, Dict[str, Any]]] = []
    for tie, key, mode, data, st in template_rows:
        sz = len(data)
        if limitless or sz <= limit:
            pool.append((sz, tie, key, mode, data, st))
        else:
            rejected.append(
                {
                    "mode": key,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    pool.append((size_tar_mck, 3, _ADAPT_TAR, _MODE_RAW_TAR_ZSTD, tar_mck, row_stats))

    win_i = min(range(len(pool)), key=lambda i: (pool[i][0], pool[i][1]))
    winner = pool[win_i]
    _win_sz, _win_tie, win_key, win_mode, win_data, _win_fb = winner

    for i, entry in enumerate(pool):
        if i == win_i:
            continue
        rejected.append(
            {"mode": entry[2], "size": entry[0], "reason": "larger_than_selected"}
        )

    col_best = min(len(columnar_v1_result), len(columnar_v2_result))
    final_sz = len(win_data)

    if win_key == _ADAPT_COL_V1:
        col_profile: Optional[str] = _COLUMN_ENCODE_PROFILE_V1
    elif win_key == _ADAPT_COL_V2:
        col_profile = _COLUMN_ENCODE_PROFILE_V2
    else:
        col_profile = None

    meta: Dict[str, Any] = {
        "candidate_sizes": dict(candidate_sizes),
        "selected_mode": win_key,
        "rejected_modes": rejected,
        "selection_reason": (
            "adaptive_smallest_mck_among_threshold_eligible_templates_plus_tar; "
            "tiebreak_order_row_then_col_v2_then_col_v1_then_tar"
        ),
        "savings_vs_tar_zstd_bytes": int(tarzstd_size - final_sz),
        "savings_vs_row_bytes": int(len(row_result) - final_sz),
        "savings_vs_columnar_bytes": int(col_best - final_sz),
        "adaptive_columnar_profile": col_profile,
    }

    chose_raw = win_key == _ADAPT_TAR
    if chose_raw:
        fb_stats = row_stats
    elif win_key == _ADAPT_COL_V1:
        fb_stats = columnar_v1_stats
    elif win_key == _ADAPT_COL_V2:
        fb_stats = columnar_v2_stats
    else:
        fb_stats = row_stats

    return win_data, win_mode, chose_raw, meta, fb_stats


def _adaptive_v2_pick(
    *,
    tarzstd_bytes: bytes,
    tarzstd_size: int,
    tolerance_vs_tar: float,
    row_pack: Optional[Tuple[bytes, Dict[str, Any]]],
    columnar_v2_pack: Optional[Tuple[bytes, Dict[str, Any]]],
    hybrid_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None,
    field_aware_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None,
    string_pattern_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None,
    pipeline_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None,
    relational_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None,
    candidate_bias: Optional[Dict[str, int]] = None,
    eligibility_multiplier: float = 1.0,
) -> Tuple[bytes, str, bool, Dict[str, Any], Dict[str, Any]]:
    """Choose smallest MCK among built template pipelines and TAR."""
    tar_mck = _build_raw_tarzstd_archive(tarzstd_bytes)
    size_tar_mck = len(tar_mck)
    limit = (
        tarzstd_size * _CORPUS_FALLBACK_THRESHOLD * max(0.25, eligibility_multiplier)
    )
    limitless = math.isinf(limit)
    bias_map = candidate_bias or {}

    rejected: List[Dict[str, Any]] = []
    pool: List[Tuple[int, int, str, str, bytes, Dict[str, Any]]] = []
    tie = 0

    if row_pack is not None:
        row_data, row_stats = row_pack
        sz = len(row_data)
        if limitless or sz <= limit:
            pool.append((sz, tie, _ADAPT_ROW, _MODE_ROW_V1, row_data, row_stats))
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_ROW,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    # Tie-break when sizes tie: row < hybrid < columnar v2 < field_aware <
    # string_pattern < pipeline_columnar_v1 < relational_encoding_v1 < TAR.
    if hybrid_pack is not None:
        hy_data, hy_stats = hybrid_pack
        sz = len(hy_data)
        if limitless or sz <= limit:
            pool.append(
                (
                    sz,
                    tie,
                    _ADAPT_HYBRID,
                    _MODE_HYBRID_ROW_COLUMNAR_V1,
                    hy_data,
                    hy_stats,
                )
            )
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_HYBRID,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    if columnar_v2_pack is not None:
        col_data, col_stats = columnar_v2_pack
        sz = len(col_data)
        if limitless or sz <= limit:
            pool.append(
                (sz, tie, _ADAPT_COL_V2, _MODE_COLUMNAR_V2, col_data, col_stats)
            )
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_COL_V2,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    if field_aware_pack is not None:
        fa_data, fa_stats = field_aware_pack
        sz = len(fa_data)
        if limitless or sz <= limit:
            pool.append(
                (sz, tie, _ADAPT_FIELD_AWARE, _MODE_COLUMNAR_V2, fa_data, fa_stats)
            )
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_FIELD_AWARE,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    if string_pattern_pack is not None:
        sp_data, sp_stats = string_pattern_pack
        sz = len(sp_data)
        if limitless or sz <= limit:
            pool.append(
                (sz, tie, _ADAPT_STRING_PATTERN, _MODE_COLUMNAR_V2, sp_data, sp_stats)
            )
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_STRING_PATTERN,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    if pipeline_pack is not None:
        pl_data, pl_stats = pipeline_pack
        sz = len(pl_data)
        if limitless or sz <= limit:
            pool.append(
                (sz, tie, _ADAPT_PIPELINE, _MODE_COLUMNAR_V2, pl_data, pl_stats)
            )
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_PIPELINE,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    if relational_pack is not None:
        rel_data, rel_stats = relational_pack
        sz = len(rel_data)
        if limitless or sz <= limit:
            pool.append(
                (sz, tie, _ADAPT_RELATIONAL, _MODE_COLUMNAR_V2, rel_data, rel_stats)
            )
            tie += 1
        else:
            rejected.append(
                {
                    "mode": _ADAPT_RELATIONAL,
                    "size": sz,
                    "reason": "above_fallback_threshold_vs_tarzstd",
                }
            )

    fb_for_tar = (
        row_pack[1]
        if row_pack is not None
        else (
            columnar_v2_pack[1]
            if columnar_v2_pack is not None
            else (
                hybrid_pack[1]
                if hybrid_pack is not None
                else (
                    field_aware_pack[1]
                    if field_aware_pack is not None
                    else (
                        string_pattern_pack[1]
                        if string_pattern_pack is not None
                        else (
                            pipeline_pack[1]
                            if pipeline_pack is not None
                            else (
                                relational_pack[1]
                                if relational_pack is not None
                                else {"fallback_reason_counts": {}}
                            )
                        )
                    )
                )
            )
        )
    )
    pool.append(
        (size_tar_mck, tie, _ADAPT_TAR, _MODE_RAW_TAR_ZSTD, tar_mck, fb_for_tar)
    )

    win_i = min(
        range(len(pool)),
        key=lambda i: (pool[i][0] + int(bias_map.get(pool[i][2], 0)), pool[i][1]),
    )
    _sz, _tie, win_key, win_mode, win_data, win_fb = pool[win_i]
    for i, entry in enumerate(pool):
        if i != win_i:
            rejected.append(
                {"mode": entry[2], "size": entry[0], "reason": "larger_than_selected"}
            )

    chose_raw = win_key == _ADAPT_TAR
    if win_key != _ADAPT_TAR and len(win_data) > tarzstd_size * tolerance_vs_tar:
        win_data = tar_mck
        win_mode = _MODE_RAW_TAR_ZSTD
        win_key = _ADAPT_TAR
        chose_raw = True
        rejected.append(
            {
                "mode": "template_over_tar_tolerance",
                "size": pool[win_i][0],
                "reason": f"exceeds_tar_times_{tolerance_vs_tar:.4f}",
            }
        )

    row_sz = len(row_pack[0]) if row_pack else None
    col_sz = len(columnar_v2_pack[0]) if columnar_v2_pack else None
    hybrid_sz = len(hybrid_pack[0]) if hybrid_pack else None
    field_aware_sz = len(field_aware_pack[0]) if field_aware_pack else None
    string_pattern_sz = len(string_pattern_pack[0]) if string_pattern_pack else None
    pipeline_sz = len(pipeline_pack[0]) if pipeline_pack else None
    relational_sz = len(relational_pack[0]) if relational_pack else None
    tpl_sizes_only = [
        s
        for s in (
            row_sz,
            col_sz,
            hybrid_sz,
            field_aware_sz,
            string_pattern_sz,
            pipeline_sz,
            relational_sz,
        )
        if s is not None
    ]
    tpl_best = min(tpl_sizes_only) if tpl_sizes_only else size_tar_mck

    final_sz = len(win_data)
    savings_row = int(row_sz - final_sz) if row_sz is not None else 0
    if win_key == _ADAPT_FIELD_AWARE:
        col_prof: Optional[str] = _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2
    elif win_key == _ADAPT_STRING_PATTERN:
        col_prof = _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1
    elif win_key == _ADAPT_PIPELINE:
        col_prof = _COLUMN_ENCODE_PROFILE_PIPELINE_V1
    elif win_key == _ADAPT_RELATIONAL:
        col_prof = _COLUMN_ENCODE_PROFILE_RELATIONAL_V1
    elif win_key in (_ADAPT_COL_V2, _ADAPT_HYBRID):
        col_prof = _COLUMN_ENCODE_PROFILE_V2
    else:
        col_prof = None
    meta: Dict[str, Any] = {
        "candidate_sizes": {
            k: v
            for k, v in (
                (_ADAPT_ROW, row_sz),
                (_ADAPT_COL_V2, col_sz),
                (_ADAPT_HYBRID, hybrid_sz),
                (_ADAPT_FIELD_AWARE, field_aware_sz),
                (_ADAPT_STRING_PATTERN, string_pattern_sz),
                (_ADAPT_PIPELINE, pipeline_sz),
                (_ADAPT_RELATIONAL, relational_sz),
                (_ADAPT_TAR, size_tar_mck),
            )
            if v is not None
        },
        "selected_mode": win_key,
        "rejected_modes": rejected,
        "selection_reason": "adaptive_v2_predictive_pool_plus_tar_tolerance_gate",
        "savings_vs_tar_zstd_bytes": int(tarzstd_size - final_sz),
        "savings_vs_row_bytes": savings_row,
        "savings_vs_columnar_bytes": int(tpl_best - final_sz),
        "adaptive_columnar_profile": col_prof,
        "candidate_bias": dict(bias_map),
        "eligibility_multiplier": float(eligibility_multiplier),
    }

    if win_key == _ADAPT_COL_V2 and columnar_v2_pack is not None:
        fb_stats = columnar_v2_pack[1]
    elif win_key == _ADAPT_FIELD_AWARE and field_aware_pack is not None:
        fb_stats = field_aware_pack[1]
    elif win_key == _ADAPT_STRING_PATTERN and string_pattern_pack is not None:
        fb_stats = string_pattern_pack[1]
    elif win_key == _ADAPT_PIPELINE and pipeline_pack is not None:
        fb_stats = pipeline_pack[1]
    elif win_key == _ADAPT_RELATIONAL and relational_pack is not None:
        fb_stats = relational_pack[1]
    elif win_key == _ADAPT_HYBRID and hybrid_pack is not None:
        fb_stats = hybrid_pack[1]
    elif win_key == _ADAPT_ROW and row_pack is not None:
        fb_stats = row_pack[1]
    else:
        fb_stats = fb_for_tar

    return win_data, win_mode, chose_raw, meta, fb_stats


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
    monotonic_ratio = (
        max(
            sum(1 for delta in deltas if delta >= 0),
            sum(1 for delta in deltas if delta <= 0),
        )
        / delta_count
    )
    small_step_ratio = sum(1 for delta in deltas if abs(delta) <= 16) / delta_count
    return monotonic_ratio >= 0.9 or small_step_ratio >= 0.9


def _longest_common_prefix(strings: List[str]) -> str:
    if not strings:
        return ""
    first = strings[0]
    end = len(first)
    for s in strings[1:]:
        end = min(end, len(s))
        i = 0
        while i < end and first[i] == s[i]:
            i += 1
        end = i
    return first[:end]


def _longest_common_suffix(strings: List[str]) -> str:
    if not strings:
        return ""
    rev = [s[::-1] for s in strings]
    r = _longest_common_prefix(rev)
    return r[::-1]


def _naive_epoch_seconds(dt: datetime) -> int:
    return int((dt - _NAIVE_EPOCH).total_seconds())


def _split_prefix_suffix_middles(
    values: List[str],
) -> Optional[Tuple[str, str, List[str]]]:
    if not values or not all(isinstance(v, str) for v in values):
        return None
    prefix = _longest_common_prefix(values)
    suffix = _longest_common_suffix(values)
    pl, sl = len(prefix), len(suffix)
    if pl == 0 and sl == 0:
        return None
    middles: List[str] = []
    for v in values:
        if len(v) < pl + sl:
            return None
        if v[:pl] != prefix or v[len(v) - sl :] != suffix:
            return None
        middles.append(v[pl : len(v) - sl] if sl else v[pl:])
    return prefix, suffix, middles


def _try_prefix_suffix_dictionary(values: List[str]) -> Optional[dict]:
    split = _split_prefix_suffix_middles(values)
    if split is None:
        return None
    prefix, suffix, middles = split
    inner, _meta = _encode_column_select(middles, _COLUMN_ENCODE_PROFILE_V1)
    enc = {
        "encoding": _ENCODING_PREFIX_SUFFIX_DICTIONARY,
        "prefix": prefix,
        "suffix": suffix,
        "middles": inner,
    }
    if _decode_column(enc, len(values)) != values:
        return None
    return enc


def _split_url_path_prefix(values: List[str]) -> Optional[Tuple[str, List[str]]]:
    if not values or not all(isinstance(v, str) for v in values):
        return None
    lcp = _longest_common_prefix(values)
    if "/" not in lcp:
        return None
    prefix = lcp[: lcp.rfind("/") + 1]
    if not prefix:
        return None
    for v in values:
        if not v.startswith(prefix):
            return None
    tails = [v[len(prefix) :] for v in values]
    return prefix, tails


def _try_url_path_prefix_encoding(values: List[str]) -> Optional[dict]:
    split = _split_url_path_prefix(values)
    if split is None:
        return None
    prefix, tails = split
    inner, _meta = _encode_column_select(tails, _COLUMN_ENCODE_PROFILE_V1)
    enc = {
        "encoding": _ENCODING_URL_PATH_PREFIX,
        "prefix": prefix,
        "tails": inner,
    }
    if _decode_column(enc, len(values)) != values:
        return None
    return enc


def _try_timestamp_string_delta(values: List[str]) -> Optional[dict]:
    if not values or not all(isinstance(v, str) for v in values):
        return None
    for fmt_id, fmt, pattern in _TS_STRING_DELTA_SPECS:
        if not all(pattern.fullmatch(v) for v in values):
            continue
        secs: List[int] = []
        bad = False
        for v in values:
            try:
                dt = datetime.strptime(v, fmt)
            except ValueError:
                bad = True
                break
            if dt.strftime(fmt) != v:
                bad = True
                break
            secs.append(_naive_epoch_seconds(dt))
        if bad or not secs:
            continue
        deltas: List[int] = [secs[0]]
        deltas.extend(secs[i] - secs[i - 1] for i in range(1, len(secs)))
        enc = {
            "encoding": _ENCODING_TIMESTAMP_STRING_DELTA,
            "fmt_id": fmt_id,
            "data": _encode_signed_varints(deltas),
        }
        if _decode_column(enc, len(values)) != values:
            continue
        return enc
    return None


def _string_pattern_min_occurrences(n: int) -> int:
    if n <= 0:
        return 2
    return max(2, n // 100 + 1)


def _discover_string_pattern_tokens(values: List[str]) -> List[str]:
    """Return up to :data:`_STRING_PATTERN_MAX_TOKENS` tokens, greedy-longest order."""
    n = len(values)
    if n == 0:
        return []
    min_o = _string_pattern_min_occurrences(n)
    counts: Dict[str, int] = {}

    for tok in _STRING_PATTERN_CATALOG:
        occ = 0
        for v in values:
            if tok in v:
                occ += 1
        if occ >= min_o:
            counts[tok] = occ

    pfx = _longest_common_prefix(values)
    if len(pfx) >= 4:
        counts[pfx] = max(counts.get(pfx, 0), n)

    sfx = _longest_common_suffix(values)
    if len(sfx) >= 4:
        counts[sfx] = max(counts.get(sfx, 0), n)

    if n >= 2:
        wc: Dict[str, int] = {}
        for v in values:
            seen_row: Set[str] = set()
            for part in _STRING_PATTERN_WORD_RE.split(v):
                if len(part) < 3:
                    continue
                if part in seen_row:
                    continue
                seen_row.add(part)
                wc[part] = wc.get(part, 0) + 1
        for w, c in wc.items():
            if c >= min_o:
                counts[w] = max(counts.get(w, 0), c)

    eligible = [t for t, c in counts.items() if c >= min_o]
    if not eligible:
        return []
    eligible.sort(key=lambda t: (-len(t), t))
    return eligible[:_STRING_PATTERN_MAX_TOKENS]


def _tokenize_string_pattern_row(
    value: str,
    order: Tuple[str, ...],
    token_to_id: Dict[str, int],
) -> List[Any]:
    pos = 0
    out: List[Any] = []
    while pos < len(value):
        hit: Optional[str] = None
        for tok in order:
            if value.startswith(tok, pos):
                hit = tok
                break
        if hit is not None:
            out.append(token_to_id[hit])
            pos += len(hit)
            continue
        nxt = len(value)
        for tok in order:
            i = value.find(tok, pos)
            if i != -1 and i < nxt:
                nxt = i
        if nxt == pos:
            if pos < len(value):
                out.append(value[pos:])
            break
        out.append(value[pos:nxt])
        pos = nxt
    return out


def _decode_string_pattern_row(seq: Sequence[Any], dictionary: Sequence[str]) -> str:
    parts: List[str] = []
    for piece in seq:
        if isinstance(piece, int):
            if piece < 0 or piece >= len(dictionary):
                raise ValueError(
                    "Corrupt column encoding: string_pattern id out of range"
                )
            parts.append(str(dictionary[piece]))
        elif isinstance(piece, str):
            parts.append(piece)
        else:
            raise ValueError("Corrupt column encoding: string_pattern row piece")
    return "".join(parts)


def _try_string_pattern_encoding_v1(values: List[str]) -> Optional[dict]:
    if not values or not all(isinstance(v, str) for v in values):
        return None
    toks = _discover_string_pattern_tokens(values)
    if not toks:
        return None
    dictionary_sorted = sorted(toks)
    token_to_id = {t: i for i, t in enumerate(dictionary_sorted)}
    order = tuple(sorted(toks, key=lambda t: (-len(t), t)))
    rows = [_tokenize_string_pattern_row(v, order, token_to_id) for v in values]
    enc = {
        "encoding": _ENCODING_STRING_PATTERN_V1,
        "dictionary": dictionary_sorted,
        "rows": rows,
    }
    if _decode_column(enc, len(values)) != values:
        return None
    return enc


def _column_encoding_candidates_for_profile(
    values: List[str], profile: str
) -> List[Tuple[str, dict]]:
    """Build all column encoding candidates for *profile* (lossless wire dicts)."""
    candidates: List[Tuple[str, dict]] = []

    raw_data = msgpack.packb(values, use_bin_type=True)
    candidates.append((_ENCODING_RAW, {"encoding": _ENCODING_RAW, "data": raw_data}))

    int_values = _canonical_int_values(values)
    if int_values is not None:
        candidates.append(
            (
                _ENCODING_VARINT,
                {
                    "encoding": _ENCODING_VARINT,
                    "data": _encode_signed_varints(int_values),
                },
            )
        )
        if len(int_values) >= 2 and _is_delta_friendly(int_values):
            deltas = [int_values[0]]
            deltas.extend(
                int_values[i] - int_values[i - 1] for i in range(1, len(int_values))
            )
            candidates.append(
                (
                    _ENCODING_DELTA,
                    {
                        "encoding": _ENCODING_DELTA,
                        "data": _encode_signed_varints(deltas),
                    },
                )
            )

    if (
        profile
        in (
            _COLUMN_ENCODE_PROFILE_V2,
            _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2,
            _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1,
        )
        and values
    ):
        dictionary: List[str] = []
        dictionary_ids: Dict[str, int] = {}
        indices: List[int] = []
        for value in values:
            if value not in dictionary_ids:
                dictionary_ids[value] = len(dictionary)
                dictionary.append(value)
            indices.append(dictionary_ids[value])
        if len(dictionary) < len(values):
            candidates.append(
                (
                    _ENCODING_DICTIONARY,
                    {
                        "encoding": _ENCODING_DICTIONARY,
                        "dictionary": dictionary,
                        "indices": _encode_uvarints(indices),
                    },
                )
            )

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
            candidates.append(
                (
                    _ENCODING_RLE,
                    {
                        "encoding": _ENCODING_RLE,
                        "values": run_values,
                        "counts": _encode_uvarints(run_counts),
                    },
                )
            )

    if profile in (
        _COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2,
        _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1,
    ):
        for name, maybe in (
            (
                _ENCODING_PREFIX_SUFFIX_DICTIONARY,
                _try_prefix_suffix_dictionary(values),
            ),
            (_ENCODING_URL_PATH_PREFIX, _try_url_path_prefix_encoding(values)),
            (_ENCODING_TIMESTAMP_STRING_DELTA, _try_timestamp_string_delta(values)),
        ):
            if maybe is not None:
                candidates.append((name, maybe))

    if profile == _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1:
        maybe_sp = _try_string_pattern_encoding_v1(values)
        if maybe_sp is not None:
            candidates.append((_ENCODING_STRING_PATTERN_V1, maybe_sp))

    return candidates


def _try_chained_prefix_suffix_string_pattern(values: List[str]) -> Optional[dict]:
    split = _split_prefix_suffix_middles(values)
    if split is None:
        return None
    prefix, suffix, middles = split
    sp_inner, _meta = _encode_column_select(
        middles, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1
    )
    enc = {
        "encoding": _ENCODING_CHAINED_COLUMN_V1,
        "variant": _CHAINED_VARIANT_PS_SP,
        "prefix": prefix,
        "suffix": suffix,
        "middles": sp_inner,
    }
    if _decode_column(enc, len(values)) != values:
        return None
    return enc


def _try_chained_url_path_string_pattern(values: List[str]) -> Optional[dict]:
    split = _split_url_path_prefix(values)
    if split is None:
        return None
    prefix, tails = split
    sp_inner, _meta = _encode_column_select(
        tails, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1
    )
    enc = {
        "encoding": _ENCODING_CHAINED_COLUMN_V1,
        "variant": _CHAINED_VARIANT_URL_SP,
        "prefix": prefix,
        "tails": sp_inner,
    }
    if _decode_column(enc, len(values)) != values:
        return None
    return enc


def _build_column_encoding_candidates(
    values: List[str], profile: str
) -> List[Tuple[str, dict]]:
    """Dispatch profile; ``pipeline_v1`` adds chained stages on top of string_pattern_v1."""
    if profile == _COLUMN_ENCODE_PROFILE_RELATIONAL_V1:
        return _column_encoding_candidates_for_profile(
            values, _COLUMN_ENCODE_PROFILE_PIPELINE_V1
        )
    if profile == _COLUMN_ENCODE_PROFILE_PIPELINE_V1:
        out = list(
            _column_encoding_candidates_for_profile(
                values, _COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1
            )
        )
        maybe_ps = _try_chained_prefix_suffix_string_pattern(values)
        if maybe_ps is not None:
            out.append(("chained_column_v1_prefix_suffix_sp", maybe_ps))
        maybe_url = _try_chained_url_path_string_pattern(values)
        if maybe_url is not None:
            out.append(("chained_column_v1_url_path_sp", maybe_url))
        return out
    return _column_encoding_candidates_for_profile(values, profile)


def _encode_column_select(
    values: List[str], profile: str
) -> Tuple[dict, Dict[str, Any]]:
    """Pick the smallest msgpack-stable encoding that round-trips to *values* exactly."""
    if not values:
        enc: dict = {
            "encoding": _ENCODING_RAW,
            "data": msgpack.packb([], use_bin_type=True),
        }
        return enc, {
            "candidates_tried": 1,
            "candidates_valid": 1,
            "winner": _ENCODING_RAW,
        }

    cands = _build_column_encoding_candidates(values, profile)
    scored: List[Tuple[int, str, dict]] = []
    for name, enc_dict in cands:
        decoded = _decode_column(enc_dict, len(values))
        if decoded != values:
            continue
        scored.append((_msgpack_size(enc_dict), name, enc_dict))
    if not scored:
        raise ValueError("no valid column encoding candidate")
    scored.sort(key=lambda item: (item[0], item[1]))
    _size, winner, best = scored[0]
    return best, {
        "candidates_tried": len(cands),
        "candidates_valid": len(scored),
        "winner": winner,
    }


def _encode_column(values: List[str]) -> dict:
    """Choose the smallest deterministic column encoding (v2 profile)."""
    return _encode_column_select(values, _COLUMN_ENCODE_PROFILE_V2)[0]


def _decode_column(column: dict, expected_count: int) -> List[str]:
    """Decode a column to the original string values."""
    encoding = column["encoding"]
    if encoding == _ENCODING_RAW:
        values = msgpack.unpackb(bytes(column["data"]), raw=False)
        if len(values) != expected_count:
            raise ValueError("Corrupt column encoding: raw column length mismatch")
        if any(not isinstance(value, str) for value in values):
            raise ValueError(
                "Corrupt column encoding: raw column contains non-string values"
            )
        return values

    if encoding == _ENCODING_VARINT:
        return [
            str(value)
            for value in _decode_signed_varints(bytes(column["data"]), expected_count)
        ]

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
            raise ValueError(
                "Corrupt column encoding: dictionary index out of range"
            ) from exc

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

    if encoding == _ENCODING_PREFIX_SUFFIX_DICTIONARY:
        prefix = str(column["prefix"])
        suffix = str(column["suffix"])
        middles = _decode_column(column["middles"], expected_count)
        out: List[str] = []
        for m in middles:
            out.append(prefix + str(m) + suffix)
        return out

    if encoding == _ENCODING_URL_PATH_PREFIX:
        prefix = str(column["prefix"])
        tails = _decode_column(column["tails"], expected_count)
        return [prefix + str(t) for t in tails]

    if encoding == _ENCODING_TIMESTAMP_STRING_DELTA:
        fmt_id = int(column["fmt_id"])
        fmt = _TS_FMT_BY_ID.get(fmt_id)
        if fmt is None:
            raise ValueError("Corrupt column encoding: unknown timestamp fmt_id")
        deltas = _decode_signed_varints(bytes(column["data"]), expected_count)
        if not deltas:
            return []
        secs = [deltas[0]]
        for d in deltas[1:]:
            secs.append(secs[-1] + d)
        decoded_ts: List[str] = []
        for s in secs:
            dt = _NAIVE_EPOCH + timedelta(seconds=int(s))
            decoded_ts.append(dt.strftime(fmt))
        return decoded_ts

    if encoding == _ENCODING_STRING_PATTERN_V1:
        dictionary = [
            value if isinstance(value, str) else str(value)
            for value in column["dictionary"]
        ]
        rows_raw = column["rows"]
        if len(rows_raw) != expected_count:
            raise ValueError(
                "Corrupt column encoding: string_pattern row count mismatch"
            )
        decoded_sp: List[str] = []
        for row in rows_raw:
            if not isinstance(row, (list, tuple)):
                raise ValueError(
                    "Corrupt column encoding: string_pattern row not a list"
                )
            decoded_sp.append(_decode_string_pattern_row(row, dictionary))
        return decoded_sp

    if encoding == _ENCODING_CHAINED_COLUMN_V1:
        variant = str(column["variant"])
        if variant == _CHAINED_VARIANT_PS_SP:
            prefix = str(column["prefix"])
            suffix = str(column["suffix"])
            middles = _decode_column(column["middles"], expected_count)
            return [prefix + str(m) + suffix for m in middles]
        if variant == _CHAINED_VARIANT_URL_SP:
            prefix = str(column["prefix"])
            tails = _decode_column(column["tails"], expected_count)
            return [prefix + str(t) for t in tails]
        raise ValueError(
            "Corrupt column encoding: unknown chained_column_v1 variant " f"{variant!r}"
        )

    raise ValueError(
        "Unsupported column encoding: "
        f"{encoding}. Supported encodings are: "
        f"{_ENCODING_RAW}, {_ENCODING_VARINT}, {_ENCODING_DELTA}, "
        f"{_ENCODING_DICTIONARY}, {_ENCODING_RLE}, "
        f"{_ENCODING_PREFIX_SUFFIX_DICTIONARY}, {_ENCODING_URL_PATH_PREFIX}, "
        f"{_ENCODING_TIMESTAMP_STRING_DELTA}, {_ENCODING_STRING_PATTERN_V1}, "
        f"{_ENCODING_CHAINED_COLUMN_V1}"
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

    with zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(
        output, closefd=False
    ) as compressor:
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
                fallback_reason_counts["binary"] = (
                    fallback_reason_counts.get("binary", 0) + 1
                )
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
                file_tpl_lines == 0 or file_template_rate < _MIN_FILE_TEMPLATE_RATE
            ) and file_total_lines > 0:
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
    column_profile: str,
    columnar_v2_stats: Optional[Dict[str, Any]],
    relational_stats: Optional[Dict[str, Any]],
    *,
    hybrid_dense_pick: bool = False,
) -> Tuple[int, int, str]:
    """Encode and store the active block for *tpl_id*.

    When *hybrid_dense_pick* is true (hybrid_row_columnar_v1), each block is
    stored either as standard column encodings or as a single ``dense_rows``
    table (row-major string values), whichever yields the smaller canonical
    msgpack representation for that block (strict ``<``; tie → columnar).
    Returns ``("", "dense", "columnar")`` as the third tuple element for
    hybrid stats aggregation.
    """
    block = active_blocks[tpl_id]
    if block is None:
        return 0, 0, ""

    encoded_columns: List[dict] = []
    num_encoded_columns = 0
    raw_column_fallback_count = 0
    for column_values in block["columns"]:
        encoded_column, meta = _encode_column_select(column_values, column_profile)
        encoding = encoded_column["encoding"]
        column_encoding_counts[encoding] = column_encoding_counts.get(encoding, 0) + 1
        if encoding == _ENCODING_RAW:
            raw_column_fallback_count += 1
        else:
            num_encoded_columns += 1
        encoded_columns.append(encoded_column)

        if columnar_v2_stats is not None:
            columnar_v2_stats["column_encoding_candidates"] += meta["candidates_tried"]
            v1_enc, _ = _encode_column_select(column_values, _COLUMN_ENCODE_PROFILE_V1)
            v2_enc, _ = _encode_column_select(column_values, _COLUMN_ENCODE_PROFILE_V2)
            columnar_v2_stats["column_encoding_bytes_v1"] += _msgpack_size(v1_enc)
            columnar_v2_stats["column_encoding_bytes_v2"] += _msgpack_size(v2_enc)
            if encoding == _ENCODING_DICTIONARY:
                columnar_v2_stats["dict_encoded_columns"] += 1
            elif encoding == _ENCODING_RLE:
                columnar_v2_stats["rle_encoded_columns"] += 1
            elif encoding == _ENCODING_DELTA:
                columnar_v2_stats["delta_encoded_columns"] += 1
            elif encoding == _ENCODING_VARINT:
                columnar_v2_stats["varint_encoded_columns"] += 1

    encoded_row_refs = _encode_row_refs(block["row_refs"])
    col_block = {"row_refs": encoded_row_refs, "columns": encoded_columns}
    relational_candidate: Optional[Tuple[dict, Dict[str, Any]]] = None
    if column_profile == _COLUMN_ENCODE_PROFILE_RELATIONAL_V1:
        relational_candidate = _try_relational_block(
            block=block,
            encoded_row_refs=encoded_row_refs,
            encoded_columns=encoded_columns,
            column_profile=_COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1,
        )
    if relational_candidate is not None:
        best_block = relational_candidate[0]
        block_kind = "relational"
        if relational_stats is not None:
            relational_stats["applied_count"] += 1
            relational_stats["details"].append(relational_candidate[1])
            relational_stats["estimated_gain"] += int(
                relational_candidate[1]["estimated_gain"]
            )
            relational_stats["actual_gain"] += int(
                relational_candidate[1]["actual_gain"]
            )
    else:
        best_block = col_block
        block_kind = "columnar"

    if hybrid_dense_pick:
        row_count = len(block["row_refs"])
        ncols = len(block["columns"])
        dense_rows: List[List[str]] = [
            [str(block["columns"][ci][ri]) for ci in range(ncols)]
            for ri in range(row_count)
        ]
        packer = msgpack.Packer(use_bin_type=True)
        den_block = {"row_refs": encoded_row_refs, "dense_rows": dense_rows}
        sz_c = len(packer.pack(best_block))
        sz_d = len(packer.pack(den_block))
        if sz_d < sz_c:
            stored: dict = den_block
            block_kind = "dense"
        else:
            stored = best_block
    else:
        stored = best_block

    if template_blocks[tpl_id] is None:
        template_blocks[tpl_id] = []
    template_blocks[tpl_id].append(stored)
    active_blocks[tpl_id] = None
    return num_encoded_columns, raw_column_fallback_count, block_kind


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

    with zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(
        output, closefd=False
    ) as compressor:
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


def _try_relational_block(
    *,
    block: dict,
    encoded_row_refs: dict,
    encoded_columns: List[dict],
    column_profile: str,
) -> Optional[Tuple[dict, Dict[str, Any]]]:
    """Try tuple-dictionary encoding across correlated columns in one block."""
    row_count = len(block["row_refs"])
    ncols = len(block["columns"])
    if row_count < 8 or ncols < 2:
        return None

    # Keep search bounded and deterministic; prefer early fields first.
    max_cols = min(ncols, 6)
    candidate_fields: List[Tuple[int, ...]] = []
    for size in (3, 2):
        for comb in itertools.combinations(range(max_cols), size):
            candidate_fields.append(comb)

    packer = msgpack.Packer(use_bin_type=True)
    baseline_block = {"row_refs": encoded_row_refs, "columns": encoded_columns}
    baseline_size = len(packer.pack(baseline_block))
    best: Optional[Tuple[int, dict, Dict[str, Any]]] = None

    for fields in candidate_fields:
        tuples: List[Tuple[str, ...]] = []
        tuple_to_id: Dict[Tuple[str, ...], int] = {}
        ids: List[int] = []
        for row_i in range(row_count):
            tup = tuple(str(block["columns"][ci][row_i]) for ci in fields)
            tid = tuple_to_id.get(tup)
            if tid is None:
                tid = len(tuples)
                tuple_to_id[tup] = tid
                tuples.append(tup)
            ids.append(tid)
        if len(tuples) >= row_count:
            continue

        selected = set(fields)
        other_columns: List[dict] = []
        for ci, col_enc in enumerate(encoded_columns):
            if ci in selected:
                continue
            other_columns.append({"index": ci, "column": col_enc})

        tuple_ids = _encode_uvarints(ids)
        tuple_dict_columns: List[dict] = []
        for j in range(len(fields)):
            col_vals = [t[j] for t in tuples]
            col_enc, _meta = _encode_column_select(col_vals, column_profile)
            tuple_dict_columns.append(col_enc)
        selected_baseline = sum(_msgpack_size(encoded_columns[ci]) for ci in fields)
        dict_encoded_size = sum(_msgpack_size(c) for c in tuple_dict_columns)
        estimated_gain = int(selected_baseline - (dict_encoded_size + len(tuple_ids)))

        relational_body = {
            "selected_fields": list(fields),
            "tuple_dictionary_columns": tuple_dict_columns,
            "tuple_ids": tuple_ids,
            "other_columns": other_columns,
            "num_columns": ncols,
            "tuple_count": row_count,
            "tuple_dictionary_size": len(tuples),
            "estimated_gain": estimated_gain,
        }
        candidate_block = {"row_refs": encoded_row_refs, "relational": relational_body}
        candidate_size = len(packer.pack(candidate_block))
        actual_gain = baseline_size - candidate_size
        if actual_gain <= 0:
            continue
        relational_body["actual_gain"] = actual_gain
        if best is None or candidate_size < best[0]:
            best = (
                candidate_size,
                candidate_block,
                {
                    "selected_tuple_fields": [int(i) for i in fields],
                    "tuple_count": row_count,
                    "tuple_dictionary_size": len(tuples),
                    "estimated_gain": estimated_gain,
                    "actual_gain": actual_gain,
                },
            )

    if best is None:
        return None
    return best[1], best[2]


def _build_columnar_template_archive(
    all_files: List[Path],
    file_meta: List[Tuple[str, bool]],
    tok_cache: Dict[str, _LineAnalysis],
    tpl_to_id: Dict[Tuple[str, ...], int],
    tpl_strings: List[str],
    column_profile: str = _COLUMN_ENCODE_PROFILE_V2,
    collect_columnar_v2_stats: bool = False,
    *,
    archive_mode: str = _MODE_COLUMNAR_V2,
    hybrid_dense_pick: bool = False,
) -> Tuple[bytes, dict]:
    """Build the block-flushed columnar (or hybrid v1) corpus-template archive."""
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

    columnar_v2_stats: Optional[Dict[str, Any]] = None
    if collect_columnar_v2_stats:
        columnar_v2_stats = {
            "column_encoding_candidates": 0,
            "column_encoding_bytes_v1": 0,
            "column_encoding_bytes_v2": 0,
            "dict_encoded_columns": 0,
            "rle_encoded_columns": 0,
            "delta_encoded_columns": 0,
            "varint_encoded_columns": 0,
        }

    hybrid_dense_block_count = 0
    hybrid_column_block_count = 0
    relational_stats: Optional[Dict[str, Any]] = None
    if column_profile == _COLUMN_ENCODE_PROFILE_RELATIONAL_V1:
        relational_stats = {
            "applied_count": 0,
            "estimated_gain": 0,
            "actual_gain": 0,
            "details": [],
        }

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
            fallback_reason_counts["binary"] = (
                fallback_reason_counts.get("binary", 0) + 1
            )
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
            file_tpl_lines == 0 or file_template_rate < _MIN_FILE_TEMPLATE_RATE
        ) and file_total_lines > 0:
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

        files_payload.append(
            {"path": rel, "kind": "text", "num_lines": file_total_lines}
        )
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
                encoded_count, raw_count, hkind = _finalize_columnar_block(
                    template_blocks,
                    active_blocks,
                    tpl_id,
                    column_encoding_counts,
                    column_profile,
                    columnar_v2_stats,
                    relational_stats,
                    hybrid_dense_pick=hybrid_dense_pick,
                )
                num_encoded_columns += encoded_count
                raw_column_fallback_count += raw_count
                if hkind == "dense":
                    hybrid_dense_block_count += 1
                elif hkind in ("columnar", "relational"):
                    hybrid_column_block_count += 1

    t_serialize_start = time.perf_counter()
    num_columnar_templates = 0

    for tpl_id, block in enumerate(active_blocks):
        if block is not None:
            encoded_count, raw_count, hkind = _finalize_columnar_block(
                template_blocks,
                active_blocks,
                tpl_id,
                column_encoding_counts,
                column_profile,
                columnar_v2_stats,
                relational_stats,
                hybrid_dense_pick=hybrid_dense_pick,
            )
            num_encoded_columns += encoded_count
            raw_column_fallback_count += raw_count
            if hkind == "dense":
                hybrid_dense_block_count += 1
            elif hkind in ("columnar", "relational"):
                hybrid_column_block_count += 1

    for block_list in template_blocks:
        if block_list is None:
            continue
        num_columnar_templates += 1

    result = _pack_columnar_archive(
        mode=archive_mode,
        tpl_strings=tpl_strings,
        files_payload=files_payload,
        template_blocks=template_blocks,
        raw_files=raw_files,
        raw_lines=raw_lines,
    )
    t_serialize_s = time.perf_counter() - t_serialize_start
    t_encode_s = time.perf_counter() - t_encode_start
    out_stats: Dict[str, Any] = {
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
        "column_profile": column_profile,
    }
    if columnar_v2_stats is not None:
        out_stats["columnar_v2_detail"] = columnar_v2_stats
    if hybrid_dense_pick:
        out_stats["hybrid_dense_block_count"] = hybrid_dense_block_count
        out_stats["hybrid_column_block_count"] = hybrid_column_block_count
    if relational_stats is not None:
        out_stats["relational_encoding_v1"] = {
            "applied_count": int(relational_stats["applied_count"]),
            "estimated_gain": int(relational_stats["estimated_gain"]),
            "actual_gain": int(relational_stats["actual_gain"]),
            "details": list(relational_stats["details"]),
        }
    return result, out_stats


def _template_reuse_rate(
    tpl_count: Dict[Tuple[str, ...], int],
    total_lines: int,
) -> float:
    """Return the share of lines participating in a recurring template."""
    if total_lines <= 0:
        return 0.0
    reuse_count = sum(
        count for count in tpl_count.values() if count >= _MIN_TEMPLATE_OCCURRENCES
    )
    return reuse_count / total_lines


# ---------------------------------------------------------------------------
# Compress / decompress
# ---------------------------------------------------------------------------


def _empty_columnar_build_stats() -> Dict[str, Any]:
    return {
        "encode_s": 0.0,
        "serialize_s": 0.0,
        "num_columnar_templates": 0,
        "num_encoded_columns": 0,
        "column_encoding_counts": {},
        "raw_column_fallback_count": 0,
        "fallback_reason_counts": {},
        "template_reuse_count": 0,
        "raw_fallback_lines": 0,
        "binary_fallback_files": 0,
        "low_structure_fallback_files": 0,
        "total_var_slots": 0,
        "relational_encoding_v1": {
            "applied_count": 0,
            "estimated_gain": 0,
            "actual_gain": 0,
            "details": [],
        },
    }


def _profile_policy(profile: str) -> Dict[str, Any]:
    """Return deterministic adaptive policy knobs for a domain profile."""
    if profile == _PROFILE_LOGS:
        return {
            "name": profile,
            "candidate_bias": {
                _ADAPT_FIELD_AWARE: -12,
                _ADAPT_STRING_PATTERN: -16,
                _ADAPT_PIPELINE: -10,
                _ADAPT_RELATIONAL: -8,
                _ADAPT_TAR: 10,
            },
            "eligibility_multiplier": 1.08,
            "predictor_overrides": {
                "structure_weight": 0.18,
                "tolerance_vs_tar": 1.04,
            },
            "feature_weights": {
                "structure_score": 1.20,
                "entropy": 1.00,
                "cardinality": 1.05,
            },
        }
    if profile == _PROFILE_NGINX:
        return {
            "name": profile,
            "candidate_bias": {
                _ADAPT_FIELD_AWARE: -18,
                _ADAPT_PIPELINE: -14,
                _ADAPT_STRING_PATTERN: -6,
                _ADAPT_RELATIONAL: -4,
                _ADAPT_TAR: 8,
            },
            "eligibility_multiplier": 1.06,
            "predictor_overrides": {
                "structure_weight": 0.16,
                "tolerance_vs_tar": 1.03,
            },
            "feature_weights": {
                "structure_score": 1.10,
                "entropy": 1.00,
                "cardinality": 1.00,
            },
        }
    if profile == _PROFILE_JSON:
        return {
            "name": profile,
            "candidate_bias": {
                _ADAPT_COL_V2: -14,
                _ADAPT_PIPELINE: -8,
                _ADAPT_RELATIONAL: -8,
                _ADAPT_TAR: 6,
            },
            "eligibility_multiplier": 1.05,
            "predictor_overrides": {
                "structure_weight": 0.20,
                "tolerance_vs_tar": 1.03,
            },
            "feature_weights": {
                "structure_score": 1.25,
                "entropy": 0.95,
                "cardinality": 1.00,
            },
        }
    return {
        "name": _PROFILE_GENERIC,
        "candidate_bias": {},
        "eligibility_multiplier": 1.0,
        "predictor_overrides": {},
        "feature_weights": {
            "structure_score": 1.00,
            "entropy": 1.00,
            "cardinality": 1.00,
        },
    }


def _rank_v23_candidates(
    *,
    profile: str,
    prediction_scores: Dict[str, float],
    predictor_features: Optional[Dict[str, float]] = None,
) -> Tuple[List[str], Dict[str, float]]:
    """Rank candidate adaptive modes for v2.3 predictive-only builds."""
    row_score = float(prediction_scores.get("row_template", 1.0))
    col_score = float(prediction_scores.get("columnar_encoding_v2", 1.0))
    feats = predictor_features or {}
    prefix_similarity = max(
        0.0, min(1.0, float(feats.get("prefix_similarity_score", 0.0)))
    )
    token_avg = max(0.0, min(1.0, float(feats.get("average_token_length", 0.0)) / 24.0))
    variance = max(0.0, min(1.0, float(feats.get("field_variance_score", 0.0))))
    structure = max(0.0, min(1.0, float(feats.get("structure_stability", 0.0))))
    string_pattern_boost = (
        max(0.0, prefix_similarity - 0.45) * max(0.0, token_avg - 0.25) * 0.26
    )
    field_aware_boost = structure * max(0.0, 1.0 - abs(variance - 0.45) / 0.45) * 0.065
    columnar_boost = structure * max(0.0, 1.0 - variance) * 0.055

    strategy_scores: Dict[str, float] = {
        _ADAPT_ROW: row_score,
        _ADAPT_COL_V2: col_score - columnar_boost,
        _ADAPT_FIELD_AWARE: col_score + 0.004 - field_aware_boost,
        _ADAPT_STRING_PATTERN: col_score + 0.005 - string_pattern_boost,
        _ADAPT_PIPELINE: col_score
        + 0.006
        - (0.55 * string_pattern_boost + 0.45 * field_aware_boost),
        _ADAPT_RELATIONAL: col_score
        + 0.008
        - (0.30 * structure * (1.0 - variance) * 0.08),
    }
    ranking: List[Tuple[float, str]] = [
        (score, mode) for mode, score in strategy_scores.items()
    ]
    if profile == _PROFILE_NGINX:
        # nginx: boost string-pattern and prefix-friendly field-aware; penalize TAR via profile bias.
        ranking = [
            (
                score
                - (0.030 if mode == _ADAPT_STRING_PATTERN else 0.0)
                - (0.022 if mode == _ADAPT_FIELD_AWARE else 0.0),
                mode,
            )
            for score, mode in ranking
        ]
    elif profile == _PROFILE_LOGS:
        # logs: boost field-aware and pipeline.
        ranking = [
            (
                score
                - (0.026 if mode == _ADAPT_FIELD_AWARE else 0.0)
                - (0.024 if mode == _ADAPT_PIPELINE else 0.0),
                mode,
            )
            for score, mode in ranking
        ]
    ranking.sort(key=lambda item: (item[0], item[1]))
    return [mode for _score, mode in ranking], strategy_scores


def _fallback_row_stats_from_pass1(
    tpl_count: Dict[Tuple[str, ...], int],
    tok_cache: Dict[str, _LineAnalysis],
    total_lines: int,
    file_meta: List[Tuple[str, bool]],
) -> Dict[str, Any]:
    """Approximate row-archive counters when row mode was not built (TAR fast path)."""
    binary_fallback_files = sum(1 for _r, b in file_meta if b)
    reuse = 0
    total_var_slots = 0
    for tkey, c in tpl_count.items():
        if c < _MIN_TEMPLATE_OCCURRENCES:
            continue
        reuse += c
        nvars = 0
        for _line, a in tok_cache.items():
            if a.template_parts == tkey:
                nvars = len(a.values)
                break
        total_var_slots += nvars * c
    return {
        "template_reuse_count": reuse,
        "raw_fallback_lines": max(0, total_lines - reuse),
        "binary_fallback_files": binary_fallback_files,
        "low_structure_fallback_files": 0,
        "total_var_slots": total_var_slots,
        "fallback_reason_counts": {},
        "encode_s": 0.0,
        "serialize_s": 0.0,
    }


def compress_corpus_template(
    input_dir: Path,
    structure_v2_enabled: bool = True,
    *,
    adaptive: Literal[
        "v1",
        "v2",
        "v2.1",
        "v2.2",
        "v2.2+hybrid",
        "v2.2+field_aware",
        "v2.2+string_pattern",
        "v2.2+pipeline",
        "v2.2+relational",
        "v2.3",
    ] = "v1",
    aggression_factor: float = 1.0,
    profile: Literal["generic", "logs", "nginx", "json"] = "generic",
) -> bytes:
    """Compress all files under *input_dir* using a shared template dictionary.

    Equivalent to ``compress_corpus_template_with_metrics(input_dir)[0]``.
    """
    return compress_corpus_template_with_metrics(
        input_dir,
        structure_v2_enabled=structure_v2_enabled,
        adaptive=adaptive,
        aggression_factor=aggression_factor,
        profile=profile,
    )[0]


def compress_corpus_template_with_metrics(
    input_dir: Path,
    structure_v2_enabled: bool = True,
    *,
    adaptive: Literal[
        "v1",
        "v2",
        "v2.1",
        "v2.2",
        "v2.2+hybrid",
        "v2.2+field_aware",
        "v2.2+string_pattern",
        "v2.2+pipeline",
        "v2.2+relational",
        "v2.3",
    ] = "v1",
    aggression_factor: float = 1.0,
    profile: Literal["generic", "logs", "nginx", "json"] = "generic",
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
    structure_v2_enabled:
        Enable structure-extraction v2 for text lines.
    adaptive:
        ``"v1"`` (default): build row + columnar v1/v2 and pick the smallest
        eligible archive.  ``"v2"`` / ``"v2.1"`` / ``"v2.2"``: sample lines +
        pass-1 stats to predict which mode(s) to build; v2.1 uses component
        scores and records prediction error, while v2.2 adds structure-aware
        columnar scoring.  ``"v2.2+hybrid"`` matches v2.2 prediction but also
        builds ``hybrid_row_columnar_v1`` (per-block row-dense vs columnar pick)
        and includes it in the adaptive pool with mandatory TAR+ZSTD.
        ``"v2.2+field_aware"`` matches v2.2 prediction and adds a
        ``field_aware_columnar_v2`` columnar build (extra string encodings).
        ``"v2.2+string_pattern"`` matches v2.2 prediction and adds a
        ``string_pattern_v1`` columnar build (shared substring tokens before ZSTD).
        ``"v2.2+pipeline"`` matches v2.2 prediction and adds a ``pipeline_v1``
        columnar build (chained field-aware then string-pattern on sub-columns
        where applicable, plus the string_pattern candidate pool).
        ``"v2.2+relational"`` matches v2.2 prediction and adds a
        ``relational_encoding_v1`` columnar build that can dictionary-encode
        repeated cross-field tuples per block.
        ``"v2.3"`` uses predictive-only selection: profile-aware ranking and
        confidence-gated top-1 / top-2 candidate builds.
    aggression_factor:
        v2.1/v2.2 multiplier for confidence-aware risk-taking.  Values above
        1.0 lower score-gap thresholds and can permit high-confidence tar-guard
        skipping.

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
    policy = _profile_policy(profile)

    all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())

    predictor_sample = None
    predictor_config = None
    if adaptive in (
        "v2",
        "v2.1",
        "v2.2",
        "v2.2+hybrid",
        "v2.2+field_aware",
        "v2.2+string_pattern",
        "v2.2+pipeline",
        "v2.2+relational",
        "v2.3",
    ):
        from metacompressor.mode_selector_v2 import (
            PredictorConfigV2,
            sample_corpus_features,
        )

        predictor_config = PredictorConfigV2(aggression_factor=aggression_factor)
        if policy["predictor_overrides"]:
            predictor_config = replace(
                predictor_config, **dict(policy["predictor_overrides"])
            )
        predictor_sample = sample_corpus_features(
            input_dir,
            all_files,
            structure_v2_enabled=structure_v2_enabled,
            config=predictor_config,
        )

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
                file_legacy_tpl_count[legacy_tkey] = (
                    file_legacy_tpl_count.get(legacy_tkey, 0) + 1
                )

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
                            value_kinds=tuple(
                                "legacy" for _ in legacy_tok_cache[line][1]
                            ),
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

    tarzstd_bytes = _build_tarzstd_bytes(input_dir, all_files)
    tarzstd_size = len(tarzstd_bytes)

    t_zstd_s = 0.0
    columnar_v2_detail: Dict[str, Any] = {}
    columnar_v1_size = 0
    row_result: Optional[bytes] = None
    columnar_v2_result: Optional[bytes] = None
    row_stats: Optional[Dict[str, Any]] = None
    columnar_v2_stats: Optional[Dict[str, Any]] = None

    hybrid_enabled = adaptive == "v2.2+hybrid"
    field_aware_enabled = adaptive == "v2.2+field_aware"
    string_pattern_enabled = adaptive == "v2.2+string_pattern"
    pipeline_enabled = adaptive == "v2.2+pipeline"
    relational_enabled = adaptive == "v2.2+relational"
    v23_predictive_enabled = adaptive == "v2.3"

    if adaptive in (
        "v2",
        "v2.1",
        "v2.2",
        "v2.2+hybrid",
        "v2.2+field_aware",
        "v2.2+string_pattern",
        "v2.2+pipeline",
        "v2.2+relational",
        "v2.3",
    ):
        from metacompressor.mode_selector_v2 import (
            compute_pass1_quick_stats,
            predict_mode_v2,
            predict_mode_v21,
            predict_mode_v22,
            should_skip_template_builds,
        )

        assert predictor_config is not None and predictor_sample is not None
        hybrid_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None
        field_aware_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None
        string_pattern_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None
        pipeline_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None
        relational_pack: Optional[Tuple[bytes, Dict[str, Any]]] = None
        v23_ranked_candidates: List[str] = []
        v23_built_candidate_count = 0
        v23_strategy_scores: Dict[str, float] = {}
        pass1_stats = compute_pass1_quick_stats(
            tpl_count,
            tok_cache,
            total_lines,
            file_meta,
            len(tpl_strings),
        )
        if adaptive in (
            "v2.1",
            "v2.2",
            "v2.2+hybrid",
            "v2.2+field_aware",
            "v2.2+string_pattern",
            "v2.2+pipeline",
            "v2.2+relational",
            "v2.3",
        ):
            if adaptive in (
                "v2.2",
                "v2.2+hybrid",
                "v2.2+field_aware",
                "v2.2+string_pattern",
                "v2.2+pipeline",
                "v2.2+relational",
                "v2.3",
            ):
                prediction = predict_mode_v22(
                    predictor_sample,
                    pass1_stats,
                    tarzstd_size,
                    predictor_config,
                )
            else:
                prediction = predict_mode_v21(
                    predictor_sample,
                    pass1_stats,
                    tarzstd_size,
                    predictor_config,
                )
            raw_tar_within_tolerance = (
                len(_build_raw_tarzstd_archive(tarzstd_bytes))
                <= tarzstd_size * predictor_config.tolerance_vs_tar
            )
            skip_templates = (
                prediction.primary_build == _ADAPT_TAR
                and not prediction.verify_second_template
                and not prediction.build_candidates
                and (raw_tar_within_tolerance or prediction.skip_tar_guard)
            )
        else:
            prediction = predict_mode_v2(
                predictor_sample,
                pass1_stats,
                tarzstd_size,
                predictor_config,
            )
            skip_templates = should_skip_template_builds(
                prediction, predictor_sample, predictor_config
            )
        t_encode_s = 0.0
        t_serialize_s = 0.0

        if skip_templates:
            result = _build_raw_tarzstd_archive(tarzstd_bytes)
            final_selected_mode = _MODE_RAW_TAR_ZSTD
            chose_raw_fallback = True
            row_stats = _fallback_row_stats_from_pass1(
                tpl_count, tok_cache, total_lines, file_meta
            )
            columnar_v2_stats = _empty_columnar_build_stats()
            fallback_reason_counts = dict(row_stats["fallback_reason_counts"])
            fallback_reason_counts["raw_tar_zstd"] = (
                fallback_reason_counts.get("raw_tar_zstd", 0) + 1
            )
            row_stats = dict(row_stats)
            row_stats["fallback_reason_counts"] = fallback_reason_counts
            fb_stats = row_stats
            adaptive_meta = {
                "candidate_sizes": {_ADAPT_TAR: len(result)},
                "selected_mode": _ADAPT_TAR,
                "rejected_modes": [
                    {
                        "mode": "all_templates",
                        "reason": "predictive_v2_skip_builds",
                    }
                ],
                "selection_reason": "adaptive_v2_high_confidence_tar_skip_template_builds",
                "savings_vs_tar_zstd_bytes": int(tarzstd_size - len(result)),
                "savings_vs_row_bytes": 0,
                "savings_vs_columnar_bytes": 0,
                "adaptive_columnar_profile": None,
            }
            t_extract_s = time.perf_counter() - t_extract_start
        else:
            build_row = build_col = False
            build_hybrid = hybrid_enabled
            build_field_aware = field_aware_enabled
            build_string_pattern = string_pattern_enabled
            build_pipeline = pipeline_enabled
            build_relational = relational_enabled
            if adaptive in (
                "v2.1",
                "v2.2",
                "v2.2+hybrid",
                "v2.2+field_aware",
                "v2.2+string_pattern",
                "v2.2+pipeline",
                "v2.2+relational",
                "v2.3",
            ):
                build_row = _ADAPT_ROW in prediction.build_candidates
                build_col = _ADAPT_COL_V2 in prediction.build_candidates
                if not build_row and not build_col:
                    # Low-confidence TAR still verifies the top template.
                    build_row = prediction.primary_build == _ADAPT_TAR
                if v23_predictive_enabled:
                    v23_ranked_candidates, v23_strategy_scores = _rank_v23_candidates(
                        profile=profile,
                        prediction_scores=prediction.scores,
                        predictor_features={
                            "structure_stability": float(
                                predictor_sample.structure_stability
                            ),
                            "prefix_similarity_score": float(
                                predictor_sample.prefix_similarity_score
                            ),
                            "average_token_length": float(
                                predictor_sample.average_token_length
                            ),
                            "field_variance_score": float(
                                predictor_sample.field_variance_score
                            ),
                        },
                    )
                    v23_conf = float(prediction.prediction_confidence)
                    if v23_conf >= _PREDICTIVE_V23_CONFIDENCE_HIGH:
                        v23_built_candidate_count = 1
                    elif v23_conf >= _PREDICTIVE_V23_CONFIDENCE_MEDIUM:
                        v23_built_candidate_count = 2
                    else:
                        v23_built_candidate_count = 3
                    selected_for_build = set(
                        v23_ranked_candidates[:v23_built_candidate_count]
                    )
                    # Low confidence must still build candidates; ensure at least one safe baseline.
                    if (
                        _ADAPT_ROW not in selected_for_build
                        and _ADAPT_COL_V2 not in selected_for_build
                    ):
                        selected_for_build.add(_ADAPT_COL_V2)
                    build_row = _ADAPT_ROW in selected_for_build
                    build_col = _ADAPT_COL_V2 in selected_for_build
                    build_hybrid = _ADAPT_HYBRID in selected_for_build
                    build_field_aware = _ADAPT_FIELD_AWARE in selected_for_build
                    build_string_pattern = _ADAPT_STRING_PATTERN in selected_for_build
                    build_pipeline = _ADAPT_PIPELINE in selected_for_build
                    build_relational = _ADAPT_RELATIONAL in selected_for_build
                    if (
                        build_hybrid
                        or build_field_aware
                        or build_string_pattern
                        or build_pipeline
                        or build_relational
                    ):
                        build_col = True
            elif prediction.verify_second_template:
                build_row = build_col = True
            elif prediction.primary_build == "row_template":
                build_row, build_col = True, False
            elif prediction.primary_build == "columnar_encoding_v2":
                build_row, build_col = False, True
            else:
                build_row, build_col = True, False
            if relational_enabled and not v23_predictive_enabled:
                # Relational mode requires a columnar pass to construct tuple dictionaries.
                build_col = True
            if v23_predictive_enabled and (
                build_hybrid
                or build_field_aware
                or build_string_pattern
                or build_pipeline
                or build_relational
            ):
                build_col = True

            if build_row:
                row_result, row_stats = _build_row_template_archive(
                    input_dir=input_dir,
                    all_files=all_files,
                    file_meta=file_meta,
                    tok_cache=tok_cache,
                    tpl_to_id=tpl_to_id,
                    tpl_strings=tpl_strings,
                )
                t_encode_s += float(row_stats["encode_s"])
                t_serialize_s += float(row_stats["serialize_s"])
            if build_col:
                columnar_v2_result, columnar_v2_stats = (
                    _build_columnar_template_archive(
                        all_files=all_files,
                        file_meta=file_meta,
                        tok_cache=tok_cache,
                        tpl_to_id=tpl_to_id,
                        tpl_strings=tpl_strings,
                        column_profile=_COLUMN_ENCODE_PROFILE_V2,
                        collect_columnar_v2_stats=True,
                    )
                )
                columnar_v2_detail = columnar_v2_stats.pop("columnar_v2_detail", {})
                t_encode_s += float(columnar_v2_stats["encode_s"])
                t_serialize_s += float(columnar_v2_stats["serialize_s"])
                if build_hybrid and columnar_v2_result is not None:
                    hybrid_result, hybrid_stats = _build_columnar_template_archive(
                        all_files=all_files,
                        file_meta=file_meta,
                        tok_cache=tok_cache,
                        tpl_to_id=tpl_to_id,
                        tpl_strings=tpl_strings,
                        column_profile=_COLUMN_ENCODE_PROFILE_V2,
                        collect_columnar_v2_stats=False,
                        archive_mode=_MODE_HYBRID_ROW_COLUMNAR_V1,
                        hybrid_dense_pick=True,
                    )
                    t_encode_s += float(hybrid_stats["encode_s"])
                    t_serialize_s += float(hybrid_stats["serialize_s"])
                    hybrid_pack = (hybrid_result, hybrid_stats)
                if build_field_aware and columnar_v2_result is not None:
                    fa_result, fa_stats = _build_columnar_template_archive(
                        all_files=all_files,
                        file_meta=file_meta,
                        tok_cache=tok_cache,
                        tpl_to_id=tpl_to_id,
                        tpl_strings=tpl_strings,
                        column_profile=_COLUMN_ENCODE_PROFILE_FIELD_AWARE_V2,
                        collect_columnar_v2_stats=False,
                    )
                    t_encode_s += float(fa_stats["encode_s"])
                    t_serialize_s += float(fa_stats["serialize_s"])
                    field_aware_pack = (fa_result, fa_stats)
                if build_string_pattern and columnar_v2_result is not None:
                    sp_result, sp_stats = _build_columnar_template_archive(
                        all_files=all_files,
                        file_meta=file_meta,
                        tok_cache=tok_cache,
                        tpl_to_id=tpl_to_id,
                        tpl_strings=tpl_strings,
                        column_profile=_COLUMN_ENCODE_PROFILE_STRING_PATTERN_V1,
                        collect_columnar_v2_stats=False,
                    )
                    t_encode_s += float(sp_stats["encode_s"])
                    t_serialize_s += float(sp_stats["serialize_s"])
                    string_pattern_pack = (sp_result, sp_stats)
                if build_pipeline and columnar_v2_result is not None:
                    pl_result, pl_stats = _build_columnar_template_archive(
                        all_files=all_files,
                        file_meta=file_meta,
                        tok_cache=tok_cache,
                        tpl_to_id=tpl_to_id,
                        tpl_strings=tpl_strings,
                        column_profile=_COLUMN_ENCODE_PROFILE_PIPELINE_V1,
                        collect_columnar_v2_stats=False,
                    )
                    t_encode_s += float(pl_stats["encode_s"])
                    t_serialize_s += float(pl_stats["serialize_s"])
                    pipeline_pack = (pl_result, pl_stats)
                if build_relational and columnar_v2_result is not None:
                    rel_result, rel_stats = _build_columnar_template_archive(
                        all_files=all_files,
                        file_meta=file_meta,
                        tok_cache=tok_cache,
                        tpl_to_id=tpl_to_id,
                        tpl_strings=tpl_strings,
                        column_profile=_COLUMN_ENCODE_PROFILE_RELATIONAL_V1,
                        collect_columnar_v2_stats=False,
                    )
                    t_encode_s += float(rel_stats["encode_s"])
                    t_serialize_s += float(rel_stats["serialize_s"])
                    relational_pack = (rel_result, rel_stats)
            if row_stats is None:
                assert columnar_v2_stats is not None
                row_stats = {
                    "template_reuse_count": columnar_v2_stats["template_reuse_count"],
                    "raw_fallback_lines": columnar_v2_stats["raw_fallback_lines"],
                    "binary_fallback_files": columnar_v2_stats["binary_fallback_files"],
                    "low_structure_fallback_files": columnar_v2_stats[
                        "low_structure_fallback_files"
                    ],
                    "total_var_slots": columnar_v2_stats["total_var_slots"],
                    "fallback_reason_counts": dict(
                        columnar_v2_stats["fallback_reason_counts"]
                    ),
                    "encode_s": 0.0,
                    "serialize_s": 0.0,
                }
            if columnar_v2_stats is None:
                assert row_stats is not None
                columnar_v2_stats = _empty_columnar_build_stats()

            t_extract_s = time.perf_counter() - t_extract_start

            row_pack = (
                (row_result, row_stats)
                if build_row and row_result is not None
                else None
            )
            col_pack = (
                (columnar_v2_result, columnar_v2_stats)
                if build_col and columnar_v2_result is not None
                else None
            )
            (
                result,
                final_selected_mode,
                chose_raw_fallback,
                adaptive_meta,
                fb_stats,
            ) = _adaptive_v2_pick(
                tarzstd_bytes=tarzstd_bytes,
                tarzstd_size=tarzstd_size,
                tolerance_vs_tar=(
                    float("inf")
                    if adaptive
                    in (
                        "v2.1",
                        "v2.2",
                        "v2.2+hybrid",
                        "v2.2+field_aware",
                        "v2.2+string_pattern",
                        "v2.2+pipeline",
                        "v2.2+relational",
                    )
                    and prediction.skip_tar_guard
                    else (
                        min(
                            float(predictor_config.tolerance_vs_tar),
                            _PREDICTIVE_V23_REGRESSION_GUARD,
                        )
                        if v23_predictive_enabled
                        else predictor_config.tolerance_vs_tar
                    )
                ),
                row_pack=row_pack,
                columnar_v2_pack=col_pack,
                hybrid_pack=hybrid_pack,
                field_aware_pack=field_aware_pack,
                string_pattern_pack=string_pattern_pack,
                pipeline_pack=pipeline_pack,
                relational_pack=relational_pack,
                candidate_bias=policy["candidate_bias"],
                eligibility_multiplier=float(policy["eligibility_multiplier"]),
            )
            if len(result) > int(tarzstd_size * _PREDICTIVE_V23_REGRESSION_GUARD):
                result = _build_raw_tarzstd_archive(tarzstd_bytes)
                final_selected_mode = _MODE_RAW_TAR_ZSTD
                chose_raw_fallback = True
                adaptive_meta["selected_mode"] = _ADAPT_TAR
                adaptive_meta.setdefault("rejected_modes", []).append(
                    {
                        "mode": "v23_regression_guard",
                        "reason": f"result_exceeds_tar_times_{_PREDICTIVE_V23_REGRESSION_GUARD:.4f}",
                    }
                )
            fallback_reason_counts = dict(fb_stats["fallback_reason_counts"])
            if chose_raw_fallback:
                fallback_reason_counts["raw_tar_zstd"] = (
                    fallback_reason_counts.get("raw_tar_zstd", 0) + 1
                )

        adaptive_meta = dict(adaptive_meta)
        adaptive_meta["adaptive_version"] = adaptive
        predicted_size = prediction.predicted_sizes.get(
            adaptive_meta["selected_mode"], len(result)
        )
        score_gap = prediction.reasoning.get(
            "score_gap",
            prediction.reasoning.get("confidence_gap", prediction.confidence),
        )
        predictive_v2_body: Dict[str, Any] = {
            "confidence": prediction.confidence,
            "prediction_confidence": prediction.prediction_confidence,
            "model_quality": prediction.model_quality,
            "primary_build": prediction.primary_build,
            "verify_second_template": prediction.verify_second_template,
            "scores": prediction.scores,
            "expected_compression_score": prediction.scores.get(
                adaptive_meta["selected_mode"]
            ),
            "score_components": prediction.score_components,
            "predicted_sizes": prediction.predicted_sizes,
            "predicted_size": predicted_size,
            "error": int(len(result) - predicted_size),
            "score_gap": score_gap,
            "confidence_band": prediction.confidence_band,
            "aggression_factor": predictor_config.aggression_factor,
            "skip_tar_guard": prediction.skip_tar_guard,
            "build_candidates": list(prediction.build_candidates),
            "structure_score": predictor_sample.structure_score,
            "structure_stability": predictor_sample.structure_stability,
            "structure_unique_key_sets": predictor_sample.structure_unique_key_sets,
            "structure_unique_key_set_ratio": (
                predictor_sample.structure_unique_key_set_ratio
            ),
            "structure_dominant_key_set_share": (
                predictor_sample.structure_dominant_key_set_share
            ),
            "structure_keyed_line_fraction": (
                predictor_sample.structure_keyed_line_fraction
            ),
            "structure_signal_strong": prediction.reasoning.get(
                "structure_signal_strong", False
            ),
            "feature_values": {
                "token_reuse_ratio": predictor_sample.token_reuse_ratio,
                "average_token_length": predictor_sample.average_token_length,
                "prefix_similarity_score": predictor_sample.prefix_similarity_score,
                "field_variance_score": predictor_sample.field_variance_score,
            },
            "reasoning": prediction.reasoning,
            "skipped_template_builds": skip_templates,
        }
        if v23_predictive_enabled:
            actual_mode = adaptive_meta["selected_mode"]
            try:
                predicted_rank_vs_actual = (
                    v23_ranked_candidates.index(actual_mode) + 1
                    if actual_mode in v23_ranked_candidates
                    else -1
                )
            except ValueError:
                predicted_rank_vs_actual = -1
            top1_correct = predicted_rank_vs_actual == 1
            top2_correct = 0 < predicted_rank_vs_actual <= 2
            predictive_v2_body["v23"] = {
                "ranked_candidates": list(v23_ranked_candidates),
                "built_candidate_count": int(v23_built_candidate_count),
                "confidence_high": _PREDICTIVE_V23_CONFIDENCE_HIGH,
                "confidence_medium": _PREDICTIVE_V23_CONFIDENCE_MEDIUM,
                "profile": profile,
                "strategy_scores": dict(v23_strategy_scores),
                "top1_correct": bool(top1_correct),
                "top2_correct": bool(top2_correct),
                "prediction_error": {
                    "predicted_rank_vs_actual": int(predicted_rank_vs_actual),
                    "actual_selected_mode": actual_mode,
                },
            }
        if hybrid_enabled:
            structure_score_h = float(predictor_sample.structure_score)
            if skip_templates:
                predictive_v2_body["hybrid_row_columnar_v1"] = {
                    "eligible": False,
                    "eligibility_reason": "predictive_v2_skip_template_builds",
                    "structure_score": structure_score_h,
                    "estimated_overhead_vs_columnar_v2_bytes": None,
                    "final_selected_mode": adaptive_meta["selected_mode"],
                }
            else:
                eligible = hybrid_pack is not None
                overhead: Optional[int] = None
                if eligible and columnar_v2_result is not None:
                    overhead = len(hybrid_pack[0]) - len(columnar_v2_result)
                eligibility_reason = (
                    "built_with_columnar_v2_prediction_pool"
                    if eligible
                    else (
                        "columnar_not_built_by_predictor"
                        if not build_col
                        else "hybrid_pack_unavailable"
                    )
                )
                predictive_v2_body["hybrid_row_columnar_v1"] = {
                    "eligible": eligible,
                    "eligibility_reason": eligibility_reason,
                    "structure_score": structure_score_h,
                    "estimated_overhead_vs_columnar_v2_bytes": overhead,
                    "final_selected_mode": adaptive_meta["selected_mode"],
                }
        adaptive_meta["predictive_v2"] = predictive_v2_body
        row_mode_size = len(row_result) if row_result is not None else 0
        columnar_v2_size = (
            len(columnar_v2_result) if columnar_v2_result is not None else 0
        )
        sel = adaptive_meta["selected_mode"]
        if sel == _ADAPT_HYBRID and hybrid_pack is not None:
            col_src_counts = hybrid_pack[1]
        elif sel == _ADAPT_FIELD_AWARE and field_aware_pack is not None:
            col_src_counts = field_aware_pack[1]
        elif sel == _ADAPT_STRING_PATTERN and string_pattern_pack is not None:
            col_src_counts = string_pattern_pack[1]
        elif sel == _ADAPT_PIPELINE and pipeline_pack is not None:
            col_src_counts = pipeline_pack[1]
        elif sel == _ADAPT_RELATIONAL and relational_pack is not None:
            col_src_counts = relational_pack[1]
        elif columnar_v2_result is not None and columnar_v2_stats is not None:
            col_src_counts = columnar_v2_stats
        else:
            col_src_counts = _empty_columnar_build_stats()

    else:
        row_result, row_stats = _build_row_template_archive(
            input_dir=input_dir,
            all_files=all_files,
            file_meta=file_meta,
            tok_cache=tok_cache,
            tpl_to_id=tpl_to_id,
            tpl_strings=tpl_strings,
        )
        columnar_v2_result, columnar_v2_stats = _build_columnar_template_archive(
            all_files=all_files,
            file_meta=file_meta,
            tok_cache=tok_cache,
            tpl_to_id=tpl_to_id,
            tpl_strings=tpl_strings,
            column_profile=_COLUMN_ENCODE_PROFILE_V2,
            collect_columnar_v2_stats=True,
        )
        columnar_v2_detail = columnar_v2_stats.pop("columnar_v2_detail", {})

        columnar_v1_result, columnar_v1_stats = _build_columnar_template_archive(
            all_files=all_files,
            file_meta=file_meta,
            tok_cache=tok_cache,
            tpl_to_id=tpl_to_id,
            tpl_strings=tpl_strings,
            column_profile=_COLUMN_ENCODE_PROFILE_V1,
            collect_columnar_v2_stats=False,
        )
        columnar_v1_size = len(columnar_v1_result)

        t_encode_s = (
            row_stats["encode_s"]
            + columnar_v2_stats["encode_s"]
            + columnar_v1_stats["encode_s"]
        )
        t_serialize_s = (
            row_stats["serialize_s"]
            + columnar_v2_stats["serialize_s"]
            + columnar_v1_stats["serialize_s"]
        )
        t_extract_s = time.perf_counter() - t_extract_start

        row_mode_size = len(row_result)
        columnar_v2_size = len(columnar_v2_result)

        (
            result,
            final_selected_mode,
            chose_raw_fallback,
            adaptive_meta,
            fb_stats,
        ) = _adaptive_select_output(
            tarzstd_bytes=tarzstd_bytes,
            tarzstd_size=tarzstd_size,
            row_result=row_result,
            row_stats=row_stats,
            columnar_v2_result=columnar_v2_result,
            columnar_v2_stats=columnar_v2_stats,
            columnar_v1_result=columnar_v1_result,
            columnar_v1_stats=columnar_v1_stats,
        )
        fallback_reason_counts = dict(fb_stats["fallback_reason_counts"])
        if chose_raw_fallback:
            fallback_reason_counts["raw_tar_zstd"] = (
                fallback_reason_counts.get("raw_tar_zstd", 0) + 1
            )

        sel = adaptive_meta["selected_mode"]
        col_src_counts = (
            columnar_v1_stats if sel == _ADAPT_COL_V1 else columnar_v2_stats
        )
        adaptive_meta = dict(adaptive_meta)
        adaptive_meta["adaptive_version"] = "v1"

    fallback_triggered = False
    fallback_reason: Optional[str] = None
    tar_guard_limit = int(tarzstd_size * (1.0 + _UNIVERSAL_TAR_SIZE_GUARD_EPSILON))
    if len(result) > tar_guard_limit:
        result = tarzstd_bytes
        final_selected_mode = _MODE_PLAIN_TAR_ZSTD_PASSTHROUGH
        chose_raw_fallback = True
        fallback_triggered = True
        fallback_reason = "container_overhead_guard"
        adaptive_meta = dict(adaptive_meta)
        adaptive_meta["selected_mode"] = _MODE_PLAIN_TAR_ZSTD_PASSTHROUGH
        candidate_sizes = dict(adaptive_meta.get("candidate_sizes", {}))
        candidate_sizes[_MODE_PLAIN_TAR_ZSTD_PASSTHROUGH] = len(result)
        adaptive_meta["candidate_sizes"] = candidate_sizes
        adaptive_meta.setdefault("rejected_modes", []).append(
            {
                "mode": "container_overhead_guard",
                "reason": (
                    f"selected_exceeds_tar_times_"
                    f"{(1.0 + _UNIVERSAL_TAR_SIZE_GUARD_EPSILON):.4f}"
                ),
            }
        )
        adaptive_meta["selection_reason"] = "container_overhead_guard"
        adaptive_meta["savings_vs_tar_zstd_bytes"] = int(tarzstd_size - len(result))
        fallback_reason_counts = dict(fallback_reason_counts)
        fallback_reason_counts["container_overhead_guard"] = (
            fallback_reason_counts.get("container_overhead_guard", 0) + 1
        )

    assert row_stats is not None
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
        "fallback_triggered": fallback_triggered,
        "fallback_reason": fallback_reason,
        "tar_size_guard_epsilon": _UNIVERSAL_TAR_SIZE_GUARD_EPSILON,
        "columnar_enabled": True,
        "columnar_v2_enabled": True,
        "num_columnar_templates": col_src_counts["num_columnar_templates"],
        "num_encoded_columns": col_src_counts["num_encoded_columns"],
        "column_encoding_counts": col_src_counts["column_encoding_counts"],
        "column_encoding_selected_counts": dict(
            col_src_counts["column_encoding_counts"]
        ),
        "raw_column_fallback_count": col_src_counts["raw_column_fallback_count"],
        "columnar_size": columnar_v2_size,
        "columnar_v1_size": columnar_v1_size,
        "row_mode_size": row_mode_size,
        "columnar_savings_vs_row": row_mode_size - columnar_v2_size,
        "final_selected_mode": final_selected_mode,
        "candidate_sizes": adaptive_meta["candidate_sizes"],
        "selected_mode": adaptive_meta["selected_mode"],
        "rejected_modes": adaptive_meta["rejected_modes"],
        "selection_reason": adaptive_meta["selection_reason"],
        "savings_vs_tar_zstd_bytes": adaptive_meta["savings_vs_tar_zstd_bytes"],
        "savings_vs_row_bytes": adaptive_meta["savings_vs_row_bytes"],
        "savings_vs_columnar_bytes": adaptive_meta["savings_vs_columnar_bytes"],
        "adaptive_columnar_profile": adaptive_meta["adaptive_columnar_profile"],
        "adaptive_version": adaptive_meta.get("adaptive_version", "v1"),
        "selected_profile": policy["name"],
        "strategy_weights_used": {
            "feature_weights": dict(policy["feature_weights"]),
            "candidate_bias": dict(policy["candidate_bias"]),
            "eligibility_multiplier": float(policy["eligibility_multiplier"]),
            "predictor_overrides": dict(policy["predictor_overrides"]),
        },
        "predictive_v2": adaptive_meta.get("predictive_v2"),
        "relational_encoding_v1": (
            col_src_counts.get(
                "relational_encoding_v1",
                {
                    "applied_count": 0,
                    "estimated_gain": 0,
                    "actual_gain": 0,
                    "details": [],
                },
            )
            if isinstance(col_src_counts, dict)
            else {
                "applied_count": 0,
                "estimated_gain": 0,
                "actual_gain": 0,
                "details": [],
            }
        ),
        "hybrid_row_columnar_v1": (
            (adaptive_meta.get("predictive_v2") or {}).get("hybrid_row_columnar_v1")
            if hybrid_enabled
            else None
        ),
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
    if columnar_v2_detail:
        metrics["column_encoding_candidates"] = columnar_v2_detail[
            "column_encoding_candidates"
        ]
        b1 = columnar_v2_detail["column_encoding_bytes_v1"]
        b2 = columnar_v2_detail["column_encoding_bytes_v2"]
        metrics["columnar_v2_savings_vs_v1_columns"] = b1 - b2
        metrics["dict_encoded_columns"] = columnar_v2_detail["dict_encoded_columns"]
        metrics["rle_encoded_columns"] = columnar_v2_detail["rle_encoded_columns"]
        metrics["delta_encoded_columns"] = columnar_v2_detail["delta_encoded_columns"]
        metrics["varint_encoded_columns"] = columnar_v2_detail["varint_encoded_columns"]
    else:
        metrics["column_encoding_candidates"] = 0
        metrics["columnar_v2_savings_vs_v1_columns"] = 0
        metrics["dict_encoded_columns"] = 0
        metrics["rle_encoded_columns"] = 0
        metrics["delta_encoded_columns"] = 0
        metrics["varint_encoded_columns"] = 0
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
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dctx = zstd.ZstdDecompressor()
    extracted: List[str] = []

    # Passthrough path: plain TAR+ZSTD payload (no MCK magic/header).
    if len(data) < 5 or data[:4] != MAGIC:
        try:
            with dctx.stream_reader(io.BytesIO(data)) as reader:
                tar_bytes = reader.read()
        except zstd.ZstdError as exc:
            raise ValueError(f"Invalid magic bytes: {data[:4]!r}") from exc
        buf = io.BytesIO(tar_bytes)
        try:
            with tarfile.open(fileobj=buf, mode="r") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        out_path = output_dir / member.name
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        f = tar.extractfile(member)
                        if f is not None:
                            out_path.write_bytes(f.read())
                        extracted.append(member.name)
        except tarfile.TarError as exc:
            raise ValueError("Invalid passthrough TAR+ZSTD payload") from exc
        return extracted

    version = data[4]
    if version != VERSION:
        raise ValueError(f"Unsupported .mck version: {version}")

    try:
        with dctx.stream_reader(io.BytesIO(data[5:])) as reader:
            raw_payload = reader.read()
    except zstd.ZstdError as exc:
        raise ValueError(f"Zstandard decompression failed: {exc}") from exc

    payload = msgpack.unpackb(raw_payload, raw=False)

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

    if mode in (_MODE_COLUMNAR_V1, _MODE_COLUMNAR_V2, _MODE_HYBRID_ROW_COLUMNAR_V1):
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
                if "dense_rows" in block:
                    dense_rows = block["dense_rows"]
                    if not isinstance(dense_rows, list) or len(dense_rows) != row_count:
                        raise ValueError(
                            "Corrupt hybrid/columnar archive: dense_rows length mismatch"
                        )
                    decoded_columns = None
                elif "relational" in block:
                    dense_rows = None
                    rel = block["relational"]
                    selected_fields = [int(i) for i in rel["selected_fields"]]
                    dict_cols_enc = rel["tuple_dictionary_columns"]
                    tuple_dictionary_size = int(rel["tuple_dictionary_size"])
                    decoded_dict_cols = [
                        _decode_column(col, tuple_dictionary_size)
                        for col in dict_cols_enc
                    ]
                    tuple_dictionary: List[List[str]] = [
                        [decoded_dict_cols[j][i] for j in range(len(decoded_dict_cols))]
                        for i in range(tuple_dictionary_size)
                    ]
                    tuple_ids = _decode_uvarints(bytes(rel["tuple_ids"]), row_count)
                    ncols = int(rel["num_columns"])
                    decoded_columns = [[""] * row_count for _ in range(ncols)]
                    for ri, tid in enumerate(tuple_ids):
                        if tid >= len(tuple_dictionary):
                            raise ValueError(
                                "Corrupt relational block: tuple id out of range"
                            )
                        tup = tuple_dictionary[tid]
                        if len(tup) != len(selected_fields):
                            raise ValueError(
                                "Corrupt relational block: tuple arity mismatch"
                            )
                        for j, ci in enumerate(selected_fields):
                            decoded_columns[ci][ri] = str(tup[j])
                    for entry in rel["other_columns"]:
                        ci = int(entry["index"])
                        col_vals = _decode_column(entry["column"], row_count)
                        decoded_columns[ci] = col_vals
                else:
                    dense_rows = None
                    decoded_columns = [
                        _decode_column(column, row_count) for column in block["columns"]
                    ]
                for row_index, row_ref in enumerate(row_refs):
                    file_id, line_index = row_ref
                    if dense_rows is not None:
                        row_vals = dense_rows[row_index]
                        if not isinstance(row_vals, list):
                            raise ValueError(
                                "Corrupt hybrid archive: dense_rows row is not a list"
                            )
                        values = [str(v) for v in row_vals]
                    else:
                        assert decoded_columns is not None
                        values = [
                            decoded_columns[column_index][row_index]
                            for column_index in range(len(decoded_columns))
                        ]
                    lines = file_lines[file_id]
                    if lines is None:
                        raise ValueError(
                            "Corrupt columnar archive: template row for raw file"
                        )
                    lines[line_index] = _reconstruct_line(templates[tpl_id], values)

        for file_id, file_entry in enumerate(files):
            rel_path = file_entry["path"]
            if file_entry["kind"] == "raw":
                file_bytes = raw_files[file_entry["raw_file_id"]]
            else:
                lines = file_lines[file_id]
                if lines is None:
                    raise ValueError(
                        "Corrupt columnar archive: incomplete file reconstruction"
                    )
                if any(line is None for line in lines):
                    raise ValueError(
                        "Corrupt columnar archive: incomplete file reconstruction"
                    )
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
