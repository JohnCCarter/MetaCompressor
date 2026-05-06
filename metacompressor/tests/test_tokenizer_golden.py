"""Golden differential test: combined-regex tokeniser ≡ legacy 12-pattern loop.

The production tokeniser ``_find_next_variable`` was changed to call a single
combined regex with named groups instead of 12 separate ``re.search`` calls.
The change is a pure performance refactor and MUST produce byte-identical
template extraction — any divergence in tokenisation propagates to template
keys, template counts, and ultimately the final compressed archive bytes.

This test runs ``_scan_text_line`` once with the production
``_find_next_variable`` and once with ``_find_next_variable_legacy`` on a
representative fixture corpus and asserts equality of the full
``_LineAnalysis`` (template_parts, values, value_kinds) for every line.

Failure mode:
    - Indicates the combined-regex priority order is wrong, or that the
      "leftmost-then-longest" tie-break is not reproduced.
    - Add the offending line to the fixture, then either reorder the
      patterns in ``_GENERIC_VAR_PATTERNS`` or revert to the legacy loop.
"""

from __future__ import annotations

import metacompressor.corpus_template as ct
from metacompressor.corpus_template import _scan_text_line

# Representative log lines covering every variable category and several
# documented edge cases.  Add new cases here whenever a real-world line
# trips the tokeniser.
FIXTURE_LINES = [
    # Plain text — no variables
    "INFO server started",
    "READY",
    # ISO 8601 timestamp + key=value structured log
    "2024-01-15T10:23:45.123Z INFO svc-foo req=req_deadbeef user=u_42 latency=120ms",
    "2024-01-15T10:23:45+02:00 ERROR something broke",
    # Apache-style timestamp
    "[15/Jan/2024:10:23:45 +0000] GET /api/v1/users 200 1234",
    # UUID v4
    "request_id=550e8400-e29b-41d4-a716-446655440000 user processing",
    "trace=abcdef12-3456-7890-abcd-ef0123456789 spans:5",
    # IPv4 with and without port
    "client 192.168.1.42 connected from 10.0.0.1:8443",
    "remote=203.0.113.5:443 forwarded=198.51.100.7",
    # IPv6
    "node=2001:0db8:85a3:0000:0000:8a2e:0370:7334 alive",
    "::1 loopback",
    # URLs and queries
    "GET https://api.example.com/v1/users HTTP/1.1",
    "request: http://internal/health?check=full&timeout=5",
    "callback=https://example.org/cb?token=xyz redirected",
    # Email
    "user owner=ops@example.com fired alert",
    "to=alice@example.com,bob@example.com sent",
    # Hex
    "addr=0xdeadbeef offset=0x1234 size=0xff",
    "fingerprint a1b2c3d4e5f60718 verified",
    # Numbers, signed/float/scientific
    "latency=120ms max=999.9 min=-3.14e-2 count=0",
    "pi=3.14159 ratio=1e10",
    # Path-ish
    "loaded /etc/conf/app.yaml from /var/lib/app/data",
    "file ./relative/path.txt missing",
    # Request-id / trace patterns
    "req_abc123 user_xyz456 session_token123",
    # Mixed: timestamps + UUIDs + numbers + IPs all on one line
    (
        "2024-01-15T10:00:00Z svc-api req=req_abcdef user=u_42 "
        "client=192.168.1.1 trace=550e8400-e29b-41d4-a716-446655440000 "
        "lat=42ms status=200 path=/v1/items/99"
    ),
    # Quoted values
    'msg="hello world" user="alice" count=3',
    "config key='deep nested value' enabled=true",
    # Bracketed / array-like value
    "tags=[red,green,blue] count=3",
    # Number adjacent to non-digit (boundary cases)
    "x=42abc y=3.14 z=-5",
    # Overlap candidates: timestamp starts with year that could match number
    "2024-01-15 INFO startup done",
    # URL adjacent to text
    "see https://docs.example.com/page#section for details",
    # Empty string
    "",
    # Only whitespace
    "   ",
    # Long random-ish line with many variables
    " ".join(f"k{i}=v_{i:04x}" for i in range(20)),
]


def test_combined_regex_matches_legacy_per_line():
    """Each fixture line must produce the same _LineAnalysis under both
    implementations of _find_next_variable.

    The test temporarily forces the Python path (so legacy and combined
    are compared on equal footing without the native dispatch in
    _scan_text_line interfering) and the production combined-regex path,
    then restores both.
    """
    saved_native = ct._NATIVE_TOKENIZER
    saved_find = ct._find_next_variable
    try:
        ct._NATIVE_TOKENIZER = None  # force the pure-Python scan path
        ct._find_next_variable = ct._find_next_variable_legacy
        legacy_results = [_scan_text_line(line) for line in FIXTURE_LINES]
        ct._find_next_variable = saved_find
        new_results = [_scan_text_line(line) for line in FIXTURE_LINES]
    finally:
        ct._NATIVE_TOKENIZER = saved_native
        ct._find_next_variable = saved_find

    for line, legacy, new in zip(FIXTURE_LINES, legacy_results, new_results):
        assert new.template_parts == legacy.template_parts, (
            f"template_parts mismatch on line {line!r}\n"
            f"  legacy: {legacy.template_parts}\n"
            f"  new:    {new.template_parts}"
        )
        assert new.values == legacy.values, (
            f"values mismatch on line {line!r}\n"
            f"  legacy: {legacy.values}\n"
            f"  new:    {new.values}"
        )
        assert new.value_kinds == legacy.value_kinds, (
            f"value_kinds mismatch on line {line!r}\n"
            f"  legacy: {legacy.value_kinds}\n"
            f"  new:    {new.value_kinds}"
        )


def test_native_tokenizer_matches_python_per_line():
    """When the native (Rust) tokenizer is installed, its output for each
    fixture line must be byte-identical to the pure-Python path.

    Skipped when ``mc_tokenizer_rs`` is not importable so non-x86_64-Linux
    contributors don't see false failures.
    """
    import pytest

    if ct._NATIVE_TOKENIZER is None:
        pytest.skip("mc_tokenizer_rs native extension not installed")

    saved_native = ct._NATIVE_TOKENIZER
    try:
        ct._NATIVE_TOKENIZER = None  # force Python path for reference
        py_results = [_scan_text_line(line) for line in FIXTURE_LINES]
        ct._NATIVE_TOKENIZER = saved_native  # native path
        rs_results = [_scan_text_line(line) for line in FIXTURE_LINES]
    finally:
        ct._NATIVE_TOKENIZER = saved_native

    for line, py, rs in zip(FIXTURE_LINES, py_results, rs_results):
        assert rs.template_parts == py.template_parts, (
            f"template_parts mismatch on line {line!r}\n"
            f"  python: {py.template_parts}\n"
            f"  rust:   {rs.template_parts}"
        )
        assert rs.values == py.values, (
            f"values mismatch on line {line!r}\n"
            f"  python: {py.values}\n"
            f"  rust:   {rs.values}"
        )
        assert rs.value_kinds == py.value_kinds, (
            f"value_kinds mismatch on line {line!r}\n"
            f"  python: {py.value_kinds}\n"
            f"  rust:   {rs.value_kinds}"
        )
        assert rs.normalized_skeleton == py.normalized_skeleton, (
            f"normalized_skeleton mismatch on line {line!r}\n"
            f"  python: {py.normalized_skeleton}\n"
            f"  rust:   {rs.normalized_skeleton}"
        )
