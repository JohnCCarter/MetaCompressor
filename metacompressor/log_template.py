"""Log template extraction and compression.

Detects repeated log line patterns and compresses them by storing a template
once alongside per-line variable values, rather than repeating the full text
of every log line.

Example
-------
Input lines::

    "ERROR user=123 latency=45ms"
    "ERROR user=456 latency=30ms"

Extracted::

    template: "ERROR user={} latency={}ms"
    values:   [["123", "45"], ["456", "30"]]

The template string is stored once in a template dictionary; each log line is
then encoded as a ``[template_id, [val, ...]]`` pair.  Lines whose pattern
does not recur (fewer than :data:`_MIN_TEMPLATE_OCCURRENCES` occurrences) are
stored verbatim as raw records inside the same payload so that the codec is
always **lossless**.

If no template appears more than once the entire payload is stored in raw mode
(zstandard-only, no template dictionary overhead).

Public API
----------
compress_log(data)         -> bytes
decompress_log(data)       -> bytes
get_compress_mode(data)    -> str

Constants
---------
TEMPLATE_MODE_VALIDATE
    String returned by :func:`get_compress_mode` when template mode was used.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

import msgpack
import zstandard as zstd

from metacompressor.container import _zstd_threads

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

MAGIC = b"MCT\x00"
VERSION = 0x01

#: Marker string returned by :func:`get_compress_mode` for template-mode data.
TEMPLATE_MODE_VALIDATE = "TEMPLATE_MODE_VALIDATE"

_ZSTD_LEVEL = 3
_MIN_TEMPLATE_OCCURRENCES = 2

# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

# Extended variable pattern — tried in priority order (most specific first).
#
# Captures the following token types as variable slots:
#
#   UUID              8-4-4-4-12 lowercase/uppercase hex groups
#   ISO 8601 datetime YYYY-MM-DDThh:mm:ss with optional fractional seconds and
#                     timezone suffix (Z or ±HH:MM / ±HHMM)
#   IPv4 + port       dotted-quad with optional :port
#   0x hex string     0x followed by one or more hex digits
#   URL               http:// or https:// scheme followed by non-whitespace
#   Number            signed integer / float / scientific notation (existing)
#
# Using a single capturing group with re.split causes variable matches to be
# interleaved with the surrounding text in the returned list, which makes exact
# reconstruction trivial (re-interleave text parts with captured values).
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

    *template_key* is a tuple of the non-variable text fragments (the
    "skeleton" of the line).  *values* is a list of the extracted variable
    substrings in order.

    Recognised variable types (matched in priority order):
    UUID, ISO-8601 datetime, IPv4(+port), 0x-hex, URL, number.

    Reconstruction is exact: interleaving the text parts with the values
    reproduces the original line character-for-character.
    """
    parts = _VAR_RE.split(line)
    # re.split with one capturing group → [text, var, text, var, …, text]
    text_parts: Tuple[str, ...] = tuple(parts[0::2])
    var_parts: List[str] = list(parts[1::2])
    return text_parts, var_parts


def _template_string(text_parts: Tuple[str, ...]) -> str:
    """Build a human-readable template string from *text_parts*.

    Each gap between adjacent text parts is filled with ``{}`` to mark a
    variable slot, e.g. ``('ERROR user=', ' latency=', 'ms')`` →
    ``"ERROR user={} latency={}ms"``.
    """
    if len(text_parts) == 1:
        return text_parts[0]
    buf: List[str] = []
    for i, part in enumerate(text_parts):
        buf.append(part)
        if i < len(text_parts) - 1:
            buf.append("{}")
    return "".join(buf)


def _reconstruct_line(template_str: str, values: List[str]) -> str:
    """Reconstruct an original log line from *template_str* and *values*.

    Splits the template on ``{}`` placeholders and re-interleaves with
    *values*, yielding the exact original string.
    """
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


def compress_log(data: bytes) -> bytes:
    """Compress *data* using log template extraction.

    Algorithm
    ---------
    1. Attempt UTF-8 decoding; fall back to raw mode on failure.
    2. Split the text into lines (preserving trailing newline semantics).
    3. Tokenise each line into *(template_key, numeric_values)*.
    4. Count how often each template_key appears.
    5. If at least one template appears ≥ :data:`_MIN_TEMPLATE_OCCURRENCES`
       times, encode in *template mode*:
       - Store recurring templates in an indexed dictionary.
       - Encode recurring-template lines as ``[template_id, [val, …]]``.
       - Encode non-recurring lines as ``[-1, raw_line_string]``.
    6. Compare template-mode output size against raw-mode output size and
       return whichever is smaller so that template overhead never hurts
       compression when savings are marginal.
    7. Otherwise, encode in *raw mode* (zstd only, no template overhead).

    Returns
    -------
    bytes
        Serialised ``.mct`` byte string (magic + version + zstd-compressed
        msgpack payload).
    """
    # Non-text data → raw mode immediately.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _serialise({"mode": "raw", "data": data})

    if not text:
        return _serialise({"mode": "raw", "data": data})

    # Split on '\n'; the trailing element may be '' (trailing newline) or some
    # text (no trailing newline) – both cases are preserved exactly.
    lines = text.split("\n")

    # --- first pass: tokenise and count template occurrences ---------------
    tokenized: List[Tuple[Tuple[str, ...], List[str]]] = [
        _tokenize(line) for line in lines
    ]
    tpl_count: Dict[Tuple[str, ...], int] = {}
    for tkey, _ in tokenized:
        tpl_count[tkey] = tpl_count.get(tkey, 0) + 1

    # --- decide mode -------------------------------------------------------
    any_recurring = any(cnt >= _MIN_TEMPLATE_OCCURRENCES for cnt in tpl_count.values())
    if not any_recurring:
        return _serialise({"mode": "raw", "data": data})

    # --- build template dictionary (only recurring keys get an id) ---------
    # Iterate in first-occurrence order (dict insertion order) for determinism.
    tpl_to_id: Dict[Tuple[str, ...], int] = {}
    tpl_strings: List[str] = []
    for tkey in tpl_count:
        if tpl_count[tkey] >= _MIN_TEMPLATE_OCCURRENCES:
            if tkey not in tpl_to_id:
                tpl_to_id[tkey] = len(tpl_strings)
                tpl_strings.append(_template_string(tkey))

    # --- second pass: encode each line ------------------------------------
    records: List = []
    for line, (tkey, values) in zip(lines, tokenized):
        if tkey in tpl_to_id:
            records.append([tpl_to_id[tkey], values])
        else:
            records.append([-1, line])

    payload = {
        "mode": "template",
        "templates": tpl_strings,
        "records": records,
    }
    template_result = _serialise(payload)
    raw_result = _serialise({"mode": "raw", "data": data})
    # Only use template mode when it actually reduces size; otherwise fall back
    # to raw zstd so that template overhead never hurts compression.
    if len(template_result) <= len(raw_result):
        return template_result
    return raw_result


def decompress_log(data: bytes) -> bytes:
    """Decompress a ``.mct`` byte string produced by :func:`compress_log`.

    Raises
    ------
    ValueError
        On invalid magic bytes, unsupported version, or corrupt payload.
    """
    _check_header(data)
    payload = _deserialise_payload(data)

    mode = payload.get("mode", "raw")
    if mode == "raw":
        raw_data = payload["data"]
        return bytes(raw_data)

    # template mode
    templates: List[str] = payload["templates"]
    records = payload["records"]

    lines: List[str] = []
    for record in records:
        tid = record[0]
        if tid == -1:
            lines.append(record[1])
        else:
            tpl_str = templates[tid]
            values: List[str] = [str(v) for v in record[1]]
            lines.append(_reconstruct_line(tpl_str, values))

    return "\n".join(lines).encode("utf-8")


def get_compress_mode(compressed: bytes) -> str:
    """Return the compression mode string embedded in *compressed*.

    Returns :data:`TEMPLATE_MODE_VALIDATE` when template mode was used,
    ``"raw"`` otherwise.

    Raises
    ------
    ValueError
        If *compressed* is not a valid ``.mct`` byte string.
    """
    _check_header(compressed)
    payload = _deserialise_payload(compressed)
    if payload.get("mode") == "template":
        return TEMPLATE_MODE_VALIDATE
    return "raw"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialise(payload: dict, level: int = _ZSTD_LEVEL) -> bytes:
    """Pack *payload* with msgpack, compress with zstd, prepend header.

    *level* defaults to the module's ``_ZSTD_LEVEL`` (3) so that raw-mode
    payloads (plain text wrapped in msgpack) keep their full ratio.  Callers
    that pack already-structured template payloads can pass ``level=1`` to
    skip ZSTD's redundant search pass.
    """
    raw = msgpack.packb(payload, use_bin_type=True)
    cctx = zstd.ZstdCompressor(level=level, threads=_zstd_threads())
    compressed = cctx.compress(raw)
    return MAGIC + bytes([VERSION]) + compressed


def _check_header(data: bytes) -> None:
    """Raise ``ValueError`` if *data* does not start with a valid MCT header."""
    if len(data) < 5:
        raise ValueError("Data too short to be a valid .mct file")
    if data[:4] != MAGIC:
        raise ValueError(f"Invalid magic bytes: {data[:4]!r}")
    version = data[4]
    if version != VERSION:
        raise ValueError(f"Unsupported .mct version: {version}")


def _deserialise_payload(data: bytes) -> dict:
    """Decompress and unpack the msgpack payload from *data* (after header)."""
    dctx = zstd.ZstdDecompressor()
    try:
        raw = dctx.decompress(data[5:])
    except zstd.ZstdError as exc:
        raise ValueError(f"Zstandard decompression failed: {exc}") from exc
    return msgpack.unpackb(raw, raw=False)
