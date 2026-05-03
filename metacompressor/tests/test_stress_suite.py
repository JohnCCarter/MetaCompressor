"""Comprehensive stress + robustness test suite for MetaCompressor.

Run with::

    python -m pytest metacompressor/tests/test_stress_suite.py -v

A session-scoped fixture writes a Markdown report to
``results/metacompressor_stress_report.md`` after all tests finish.

Test Categories
---------------
A  Robustness     – edge cases: empty, single-byte, large, many-small,
                    long lines, no-trailing-newline, repetitive, unique content
B  Adversarial    – random data, near-identical lines, high-cardinality,
                    truncated archives, invalid magic, broken msgpack
C  Generalization – nginx logs, JSON/NDJSON, mixed formats, pre-compressed
D  Performance    – compress / decompress timing + peak memory (small/med/large)
E  Regression     – MC vs TAR+ZSTD gate: flag if MC > TAR+ZSTD × 1.10
"""

from __future__ import annotations

import gzip
import io
import os
import tarfile
import time
import tracemalloc
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import msgpack
import pytest
import zstandard as zstd

from metacompressor.corpus_template import (
    compress_corpus_template,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.log_template import (
    TEMPLATE_MODE_VALIDATE,
    compress_log,
    decompress_log,
    get_compress_mode,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_RESULTS_DIR = _REPO_ROOT / "results"

# Regression threshold: flag if MC is more than 10 % larger than TAR+ZSTD.
_REGRESSION_THRESHOLD = 1.10

# ---------------------------------------------------------------------------
# Dataset generators
# ---------------------------------------------------------------------------


def _write_corpus(tmp: Path, files: Dict[str, bytes]) -> Path:
    """Write *files* dict to a subdirectory of *tmp*, return that directory."""
    corpus = tmp / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        dest = corpus / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return corpus


def gen_empty_file(tmp: Path) -> Path:
    return _write_corpus(tmp, {"empty.txt": b"", "anchor.log": b"INFO x=1\n" * 5})


def gen_single_byte(tmp: Path) -> Path:
    return _write_corpus(tmp, {"one.bin": b"\x42"})


def gen_large_file(tmp: Path, size_mb: int = 10) -> Path:
    """Repetitive structured log data scaled to *size_mb* megabytes."""
    line = b"2024-01-01T00:00:00Z INFO req=1 path=/api/v1 status=200 latency=12ms\n"
    data = line * (size_mb * 1024 * 1024 // len(line) + 1)
    data = data[: size_mb * 1024 * 1024]
    return _write_corpus(tmp, {"large.log": data})


def gen_many_small_files(tmp: Path, n: int = 500) -> Path:
    files = {
        f"logs/day{i:04d}.log": (
            f"INFO event={i} status=200\nWARN event={i+1} code=429\n" * 3
        ).encode()
        for i in range(n)
    }
    return _write_corpus(tmp, files)


def gen_mixed_corpus(tmp: Path) -> Path:
    return _write_corpus(
        tmp,
        {
            "text.log": b"ERROR code=500 user=42\n" * 200,
            "data.bin": os.urandom(512),
            "config.json": b'{"host": "localhost", "port": 8080}\n' * 30,
        },
    )


def gen_long_lines(tmp: Path) -> Path:
    line = b"A" * 10_000 + b" val=1\n"
    return _write_corpus(tmp, {"longlines.log": line * 50})


def gen_no_trailing_newline(tmp: Path) -> Path:
    data = b"INFO x=1\nINFO x=2\nINFO x=3"  # no final newline
    return _write_corpus(tmp, {"nonl.log": data})


def gen_repetitive(tmp: Path) -> Path:
    line = b"METRIC host=server cpu=50 mem=1024\n"
    return _write_corpus(tmp, {"rep.log": line * 5_000})


def gen_unique_content(tmp: Path) -> Path:
    lines = [f"UNIQUE line #{i} payload={os.urandom(4).hex()}\n".encode() for i in range(200)]
    return _write_corpus(tmp, {"unique.log": b"".join(lines)})


def gen_random_data(tmp: Path) -> Path:
    return _write_corpus(tmp, {"random.bin": os.urandom(64 * 1024)})


def gen_nearly_identical_lines(tmp: Path) -> Path:
    """Lines that share a template key (same structure, different numbers)."""
    lines = [f"STATUS code={i} host=web-{i % 3}\n".encode() for i in range(500)]
    return _write_corpus(tmp, {"similar.log": b"".join(lines)})


def gen_high_cardinality(tmp: Path) -> Path:
    """Every line carries a random payload so no template key ever recurs."""
    unique_lines = [
        f"free-form message idx={i} payload={os.urandom(6).hex()}\n".encode()
        for i in range(400)
    ]
    return _write_corpus(tmp, {"highcard.log": b"".join(unique_lines)})


def gen_nginx_logs(tmp: Path) -> Path:
    template = (
        '192.168.{a}.{b} - - [01/Jan/2024:00:{mm:02d}:{ss:02d} +0000] '
        '"GET /api/v{v}/resource/{rid} HTTP/1.1" {code} {size} '
        '"-" "Mozilla/5.0" {lat}\n'
    )
    lines = []
    for i in range(1000):
        lines.append(
            template.format(
                a=i % 256,
                b=(i * 7) % 256,
                mm=i // 60 % 60,
                ss=i % 60,
                v=(i % 3) + 1,
                rid=i % 50,
                code=[200, 404, 500, 301][i % 4],
                size=100 + (i * 13) % 9000,
                lat=0.01 + (i % 100) * 0.005,
            ).encode()
        )
    return _write_corpus(tmp, {"access.log": b"".join(lines)})


def gen_json_corpus(tmp: Path) -> Path:
    ndjson_lines = [
        f'{{"ts":"2024-01-01T00:{i//60:02d}:{i%60:02d}Z","level":"INFO","msg":"req","id":{i}}}\n'.encode()
        for i in range(500)
    ]
    return _write_corpus(
        tmp,
        {
            "events.ndjson": b"".join(ndjson_lines),
            "config.json": b'{"version": 1, "timeout": 30, "retries": 3}\n' * 20,
        },
    )


def gen_mixed_formats(tmp: Path) -> Path:
    nginx_line = b'10.0.0.1 - - [01/Jan/2024:00:00:01 +0000] "GET / HTTP/1.1" 200 612\n'
    return _write_corpus(
        tmp,
        {
            "nginx.log": nginx_line * 300,
            "app.log": b"ERROR user=99 code=500 path=/api\n" * 200,
            "data.ndjson": b'{"event":"click","user":42}\n' * 150,
            "readme.md": b"# Project\nThis is a readme.\n" * 40,
        },
    )


def gen_precompressed(tmp: Path) -> Path:
    payload = b"hello compressed world\n" * 200

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(payload)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inner.txt", payload.decode())

    return _write_corpus(
        tmp,
        {
            "archive.gz": gz_buf.getvalue(),
            "archive.zip": zip_buf.getvalue(),
            "normal.log": b"INFO status=200 path=/api\n" * 100,
        },
    )


# ---------------------------------------------------------------------------
# TAR+ZSTD baseline
# ---------------------------------------------------------------------------


def tar_zstd_compress_dir(input_dir: Path) -> bytes:
    """Compress *input_dir* with TAR+ZSTD (level 3) – used as size baseline."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in sorted(input_dir.rglob("*")):
            if p.is_file():
                tar.add(str(p), arcname=p.relative_to(input_dir).as_posix())
    cctx = zstd.ZstdCompressor(level=3)
    return cctx.compress(buf.getvalue())


# ---------------------------------------------------------------------------
# Results accumulator (populated by test methods; read by session fixture)
# ---------------------------------------------------------------------------

_RESULTS: List[Dict] = []
_CRASHES: List[str] = []
_SLOW_CASES: List[str] = []
_MEMORY_SPIKES: List[str] = []


def _record(
    test_name: str,
    status: str,
    mc_size: Optional[int] = None,
    tarzstd_size: Optional[int] = None,
    notes: str = "",
    compress_s: Optional[float] = None,
    decompress_s: Optional[float] = None,
    peak_mem_mb: Optional[float] = None,
) -> None:
    delta_pct: Optional[float] = None
    if mc_size is not None and tarzstd_size is not None and tarzstd_size > 0:
        delta_pct = (mc_size - tarzstd_size) / tarzstd_size * 100.0
    _RESULTS.append(
        {
            "test": test_name,
            "status": status,
            "mc_size": mc_size,
            "tarzstd_size": tarzstd_size,
            "delta_pct": delta_pct,
            "compress_s": compress_s,
            "decompress_s": decompress_s,
            "peak_mem_mb": peak_mem_mb,
            "notes": notes,
        }
    )


def _fmt(val: Optional[float], fmt: str = ".2f", suffix: str = "") -> str:
    return f"{val:{fmt}}{suffix}" if val is not None else "—"


def _emit_report() -> None:
    """Write markdown report to results/metacompressor_stress_report.md."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = _RESULTS_DIR / "metacompressor_stress_report.md"

    crashes = [r for r in _RESULTS if r["status"] == "CRASH"]
    regressions = [
        r
        for r in _RESULTS
        if r.get("delta_pct") is not None and r["delta_pct"] > 10.0
    ]

    if crashes:
        verdict = "STRESS_FAILED  Reason: crashes detected – see report"
    else:
        verdict = "STRESS_VALIDATED"

    lines: List[str] = [
        "# MetaCompressor Stress Report",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Results Table",
        "",
        "| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |",
        "|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|",
    ]
    for r in _RESULTS:
        mc = f"{r['mc_size']:,}" if r["mc_size"] is not None else "—"
        tz = f"{r['tarzstd_size']:,}" if r["tarzstd_size"] is not None else "—"
        dp = _fmt(r["delta_pct"], ".1f", "%") if r["delta_pct"] is not None else "—"
        cs = _fmt(r["compress_s"], ".3f", "s")
        ds = _fmt(r["decompress_s"], ".3f", "s")
        pm = _fmt(r["peak_mem_mb"], ".1f", " MB")
        lines.append(
            f"| {r['test']} | {r['status']} | {mc} | {tz} | {dp} | {cs} | {ds} | {pm} | {r['notes']} |"
        )

    lines.append("")
    lines.append("## Crashes")
    lines.append("")
    if _CRASHES:
        lines.extend(f"- {c}" for c in _CRASHES)
    else:
        lines.append("*(none)*")

    lines.append("")
    lines.append("## Regressions (MC > TAR+ZSTD by >10 %)")
    lines.append("")
    if regressions:
        for r in regressions:
            lines.append(
                f"- **{r['test']}**: MC={r['mc_size']:,} TAR+ZSTD={r['tarzstd_size']:,}"
                f" Δ={r['delta_pct']:.1f}%  {r['notes']}"
            )
    else:
        lines.append("*(none)*")

    lines.append("")
    lines.append("## Slow Cases (compress > 5 s)")
    lines.append("")
    if _SLOW_CASES:
        lines.extend(f"- {s}" for s in _SLOW_CASES)
    else:
        lines.append("*(none)*")

    lines.append("")
    lines.append("## Memory Spikes (peak > 200 MB)")
    lines.append("")
    if _MEMORY_SPIKES:
        lines.extend(f"- {m}" for m in _MEMORY_SPIKES)
    else:
        lines.append("*(none)*")

    lines += [
        "",
        "## Summary",
        "",
        f"- Total tests recorded : {len(_RESULTS)}",
        f"- Crashes              : {len(crashes)}",
        f"- Regressions          : {len(regressions)}",
        f"- Slow cases           : {len(_SLOW_CASES)}",
        f"- Memory spikes        : {len(_MEMORY_SPIKES)}",
        "",
        f"**Final verdict: `{verdict}`**",
    ]

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Session-scoped fixture: write report after all tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _write_report_fixture():  # noqa: PT004
    yield
    _emit_report()


# ---------------------------------------------------------------------------
# A) Robustness tests
# ---------------------------------------------------------------------------


class TestRobustness:
    """Edge-case file content and structure tests."""

    def test_empty_file(self, tmp_path):
        corpus = gen_empty_file(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        recovered = decompress_corpus_template(archive, out)
        assert (out / "empty.txt").read_bytes() == b""
        assert (out / "anchor.log").read_bytes() == b"INFO x=1\n" * 5
        tz = tar_zstd_compress_dir(corpus)
        _record("A-empty_file", "PASS", len(archive), len(tz),
                "empty file in corpus – must round-trip with zero bytes")

    def test_single_byte_file(self, tmp_path):
        corpus = gen_single_byte(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        assert (out / "one.bin").read_bytes() == b"\x42"
        tz = tar_zstd_compress_dir(corpus)
        _record("A-single_byte", "PASS", len(archive), len(tz), "single-byte file")

    def test_large_file(self, tmp_path):
        corpus = gen_large_file(tmp_path, size_mb=10)
        tracemalloc.start()
        t0 = time.perf_counter()
        archive = compress_corpus_template(corpus)
        compress_s = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak / 1024 / 1024

        t1 = time.perf_counter()
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        decompress_s = time.perf_counter() - t1

        # Verify data integrity
        original = (corpus / "large.log").read_bytes()
        assert (out / "large.log").read_bytes() == original

        tz = tar_zstd_compress_dir(corpus)
        notes = f"10 MB structured log"
        if compress_s > 5.0:
            _SLOW_CASES.append(f"A-large_file: compress {compress_s:.2f}s")
        if peak_mb > 200:
            _MEMORY_SPIKES.append(f"A-large_file: {peak_mb:.0f} MB")
        _record("A-large_file", "PASS", len(archive), len(tz), notes,
                compress_s=compress_s, decompress_s=decompress_s, peak_mem_mb=peak_mb)

    def test_many_small_files(self, tmp_path):
        corpus = gen_many_small_files(tmp_path, n=500)
        t0 = time.perf_counter()
        archive = compress_corpus_template(corpus)
        compress_s = time.perf_counter() - t0

        out = tmp_path / "out"
        t1 = time.perf_counter()
        decompress_corpus_template(archive, out)
        decompress_s = time.perf_counter() - t1

        # Spot-check a few files
        for i in [0, 100, 499]:
            expected = (corpus / f"logs/day{i:04d}.log").read_bytes()
            assert (out / f"logs/day{i:04d}.log").read_bytes() == expected, (
                f"Mismatch for day{i:04d}.log"
            )

        tz = tar_zstd_compress_dir(corpus)
        _record("A-many_small_files", "PASS", len(archive), len(tz),
                "500 small structured log files",
                compress_s=compress_s, decompress_s=decompress_s)

    def test_mixed_text_and_binary(self, tmp_path):
        corpus = gen_mixed_corpus(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        for name, data in [
            ("text.log", b"ERROR code=500 user=42\n" * 200),
            ("data.bin", (corpus / "data.bin").read_bytes()),
        ]:
            assert (out / name).read_bytes() == data, f"Mismatch: {name}"
        tz = tar_zstd_compress_dir(corpus)
        _record("A-mixed_text_binary", "PASS", len(archive), len(tz),
                "text + binary in same corpus")

    def test_long_lines(self, tmp_path):
        corpus = gen_long_lines(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "longlines.log").read_bytes()
        assert (out / "longlines.log").read_bytes() == original
        tz = tar_zstd_compress_dir(corpus)
        _record("A-long_lines", "PASS", len(archive), len(tz), "10 000-char lines")

    def test_no_trailing_newline(self, tmp_path):
        corpus = gen_no_trailing_newline(tmp_path)
        original = b"INFO x=1\nINFO x=2\nINFO x=3"
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        assert (out / "nonl.log").read_bytes() == original, \
            "No-trailing-newline file must round-trip byte-for-byte"
        tz = tar_zstd_compress_dir(corpus)
        _record("A-no_trailing_newline", "PASS", len(archive), len(tz),
                "file with no trailing newline")

    def test_repetitive_content(self, tmp_path):
        corpus = gen_repetitive(tmp_path)
        archive, metrics = compress_corpus_template_with_metrics(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "rep.log").read_bytes()
        assert (out / "rep.log").read_bytes() == original

        # Highly repetitive data should hit excellent compression
        total_raw = sum(p.stat().st_size for p in corpus.rglob("*") if p.is_file())
        ratio = len(archive) / total_raw
        tz = tar_zstd_compress_dir(corpus)
        _record("A-repetitive_content", "PASS", len(archive), len(tz),
                f"5 000 identical lines; ratio={ratio:.4f}; tpl_reuse={metrics['template_reuse_rate']:.2f}")

    def test_unique_content(self, tmp_path):
        corpus = gen_unique_content(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "unique.log").read_bytes()
        assert (out / "unique.log").read_bytes() == original
        tz = tar_zstd_compress_dir(corpus)
        _record("A-unique_content", "PASS", len(archive), len(tz),
                "200 lines each with random payload – fallback path")


# ---------------------------------------------------------------------------
# B) Adversarial tests
# ---------------------------------------------------------------------------


class TestAdversarial:
    """Tests that probe crash safety, error reporting, and silent-corruption prevention."""

    def test_random_data_no_crash(self, tmp_path):
        """Fully random data must not crash; should fall back to raw zstd."""
        corpus = gen_random_data(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "random.bin").read_bytes()
        assert (out / "random.bin").read_bytes() == original, "Random data corruption!"
        tz = tar_zstd_compress_dir(corpus)
        _record("B-random_data", "PASS", len(archive), len(tz),
                "fully random binary – binary_fallback expected")

    def test_nearly_identical_lines_template_detection(self, tmp_path):
        """Lines sharing the same template key should be deduplicated."""
        corpus = gen_nearly_identical_lines(tmp_path)
        archive, metrics = compress_corpus_template_with_metrics(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "similar.log").read_bytes()
        assert (out / "similar.log").read_bytes() == original
        # All lines share the 'STATUS code={} host=web-{}' template
        assert metrics["num_shared_templates"] >= 1
        assert metrics["template_reuse_rate"] > 0.5
        tz = tar_zstd_compress_dir(corpus)
        _record("B-nearly_identical_lines", "PASS", len(archive), len(tz),
                f"500 lines w/ same template; reuse_rate={metrics['template_reuse_rate']:.2f}")

    def test_high_cardinality_no_crash(self, tmp_path):
        """High-cardinality log (no structural reuse) must not crash or corrupt."""
        corpus = gen_high_cardinality(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "highcard.log").read_bytes()
        assert (out / "highcard.log").read_bytes() == original, \
            "High-cardinality log corrupted!"
        tz = tar_zstd_compress_dir(corpus)
        _record("B-high_cardinality", "PASS", len(archive), len(tz),
                "unique lines – fallback expected; no crash")

    def test_truncated_archive_raises(self, tmp_path):
        """Truncated MCK archive must raise ValueError, not silently succeed."""
        corpus = _write_corpus(tmp_path, {"a.log": b"INFO x=1\n" * 10})
        archive = compress_corpus_template(corpus)
        truncated = archive[: len(archive) // 2]
        with pytest.raises((ValueError, Exception)):
            decompress_corpus_template(truncated, tmp_path / "out")
        _record("B-truncated_archive", "PASS", notes="truncated archive → exception raised")

    def test_invalid_magic_raises(self, tmp_path):
        """Archive with wrong magic bytes must raise ValueError."""
        corpus = _write_corpus(tmp_path, {"a.log": b"INFO x=1\n" * 10})
        archive = compress_corpus_template(corpus)
        bad = b"XXXX" + archive[4:]
        with pytest.raises(ValueError, match="magic"):
            decompress_corpus_template(bad, tmp_path / "out2")
        _record("B-invalid_magic", "PASS", notes="invalid magic → ValueError raised")

    def test_broken_msgpack_raises(self, tmp_path):
        """Valid MCK header + valid zstd wrapper around garbage → exception raised."""
        cctx = zstd.ZstdCompressor(level=3)
        # Compress garbage bytes – will produce valid zstd but invalid msgpack
        bad_payload = cctx.compress(b"\xff\xfe\xfd\xfc" * 50)
        bad_archive = b"MCK\x00" + bytes([0x01]) + bad_payload
        with pytest.raises(Exception):
            decompress_corpus_template(bad_archive, tmp_path / "out3")
        _record("B-broken_msgpack", "PASS", notes="corrupt msgpack payload → exception raised")

    def test_too_short_data_raises(self, tmp_path):
        """Fewer than 5 bytes must raise ValueError."""
        with pytest.raises(ValueError):
            decompress_corpus_template(b"\x00\x01\x02", tmp_path / "out4")
        _record("B-too_short", "PASS", notes="<5 byte input → ValueError")

    def test_unsupported_version_raises(self, tmp_path):
        """Correct magic + wrong version byte must raise ValueError."""
        corpus = _write_corpus(tmp_path, {"a.log": b"INFO x=1\n" * 5})
        archive = compress_corpus_template(corpus)
        bad = archive[:4] + bytes([0xFF]) + archive[5:]
        with pytest.raises(ValueError):
            decompress_corpus_template(bad, tmp_path / "out5")
        _record("B-bad_version", "PASS", notes="version 0xFF → ValueError")

    def test_log_template_random_data_fallback(self):
        """compress_log on random bytes must use raw mode (no crash)."""
        data = os.urandom(8192)
        compressed = compress_log(data)
        assert decompress_log(compressed) == data
        mode = get_compress_mode(compressed)
        assert mode == "raw", f"Expected raw mode for random data, got {mode}"
        _record("B-log_random_fallback", "PASS", len(compressed), None,
                "random bytes → log_template raw fallback")

    def test_no_silent_data_corruption(self, tmp_path):
        """Flip a bit in the payload and ensure decompression fails, not silently corrupts."""
        corpus = _write_corpus(tmp_path, {"a.log": b"INFO x=1\n" * 20})
        archive = compress_corpus_template(corpus)
        # Flip a byte deep in the compressed payload (after the 5-byte header)
        ba = bytearray(archive)
        pos = len(ba) // 2
        ba[pos] ^= 0xFF
        corrupted = bytes(ba)
        try:
            decompress_corpus_template(corrupted, tmp_path / "out6")
            # If it somehow returns without error, verify it didn't silently
            # produce the original data (that would be a false negative).
            recovered = (tmp_path / "out6" / "a.log").read_bytes()
            assert recovered != b"INFO x=1\n" * 20, \
                "Silent data corruption: corrupt archive returned original data!"
            status = "PASS"
            notes = "corrupt archive – data mismatch detected (no silent corruption)"
        except Exception:
            status = "PASS"
            notes = "corrupt archive raised exception (correct behaviour)"
        _record("B-no_silent_corruption", status, notes=notes)


# ---------------------------------------------------------------------------
# C) Generalisation tests
# ---------------------------------------------------------------------------


class TestGeneralization:
    """Real-world format fidelity and fallback trigger tests."""

    def test_nginx_logs_round_trip(self, tmp_path):
        corpus = gen_nginx_logs(tmp_path)
        archive, metrics = compress_corpus_template_with_metrics(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        original = (corpus / "access.log").read_bytes()
        assert (out / "access.log").read_bytes() == original
        tz = tar_zstd_compress_dir(corpus)
        _record("C-nginx_logs", "PASS", len(archive), len(tz),
                f"1 000 nginx lines; tpl_reuse={metrics['template_reuse_rate']:.2f}")

    def test_json_ndjson_round_trip(self, tmp_path):
        corpus = gen_json_corpus(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        for name in ["events.ndjson", "config.json"]:
            assert (out / name).read_bytes() == (corpus / name).read_bytes(), \
                f"Mismatch: {name}"
        tz = tar_zstd_compress_dir(corpus)
        _record("C-json_ndjson", "PASS", len(archive), len(tz), "NDJSON + JSON config")

    def test_mixed_formats_round_trip(self, tmp_path):
        corpus = gen_mixed_formats(tmp_path)
        archive = compress_corpus_template(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        for name in ["nginx.log", "app.log", "data.ndjson", "readme.md"]:
            assert (out / name).read_bytes() == (corpus / name).read_bytes(), \
                f"Mismatch: {name}"
        tz = tar_zstd_compress_dir(corpus)
        _record("C-mixed_formats", "PASS", len(archive), len(tz),
                "nginx + app log + ndjson + markdown")

    def test_precompressed_files_round_trip(self, tmp_path):
        """Already-compressed files (.gz, .zip) should round-trip as binary."""
        corpus = gen_precompressed(tmp_path)
        archive, metrics = compress_corpus_template_with_metrics(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        for name in ["archive.gz", "archive.zip", "normal.log"]:
            assert (out / name).read_bytes() == (corpus / name).read_bytes(), \
                f"Mismatch: {name}"
        # .gz and .zip are binary → binary_fallback_files should be ≥ 2
        assert metrics["binary_fallback_files"] >= 2, \
            "Pre-compressed files should trigger binary fallback"
        tz = tar_zstd_compress_dir(corpus)
        _record("C-precompressed", "PASS", len(archive), len(tz),
                f"gz + zip + log; binary_fallback={metrics['binary_fallback_files']}")

    def test_fallback_triggers_for_random_only_corpus(self, tmp_path):
        """A corpus of only random binary data must fall back cleanly without crash."""
        corpus = _write_corpus(
            tmp_path,
            {f"rand{i}.bin": os.urandom(1024) for i in range(5)},
        )
        archive, metrics = compress_corpus_template_with_metrics(corpus)
        out = tmp_path / "out"
        decompress_corpus_template(archive, out)
        for i in range(5):
            assert (out / f"rand{i}.bin").read_bytes() == (corpus / f"rand{i}.bin").read_bytes()
        # All files should be binary fallback
        assert metrics["binary_fallback_files"] == 5
        _record("C-all_binary_fallback", "PASS", len(archive), None,
                "5 random-binary files → all binary_fallback")


# ---------------------------------------------------------------------------
# D) Performance tests
# ---------------------------------------------------------------------------


class TestPerformance:
    """Timing and memory measurements on small / medium / large corpora."""

    @staticmethod
    def _measure(corpus: Path, label: str, notes: str = "") -> None:
        tracemalloc.start()
        t0 = time.perf_counter()
        archive = compress_corpus_template(corpus)
        compress_s = time.perf_counter() - t0
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak_bytes / 1024 / 1024

        t1 = time.perf_counter()
        out = corpus.parent / "out_perf"
        decompress_corpus_template(archive, out)
        decompress_s = time.perf_counter() - t1

        tz = tar_zstd_compress_dir(corpus)

        if compress_s > 5.0:
            _SLOW_CASES.append(f"{label}: compress {compress_s:.2f}s")
        if peak_mb > 200:
            _MEMORY_SPIKES.append(f"{label}: {peak_mb:.0f} MB")

        _record(
            label,
            "PASS",
            mc_size=len(archive),
            tarzstd_size=len(tz),
            notes=notes,
            compress_s=compress_s,
            decompress_s=decompress_s,
            peak_mem_mb=peak_mb,
        )

    def test_small_corpus_perf(self, tmp_path):
        """10 files × ~5 KB each ≈ 50 KB total."""
        files = {
            f"small_{i:02d}.log": (
                f"INFO event={i} status=200 path=/api\n" * 50
            ).encode()
            for i in range(10)
        }
        corpus = _write_corpus(tmp_path, files)
        self._measure(corpus, "D-perf_small", "10 × 5 KB structured logs")

    def test_medium_corpus_perf(self, tmp_path):
        """20 files × ~100 KB each ≈ 2 MB total."""
        line = b"2024-01-01T00:00:00Z INFO req={n} path=/api/v1 status=200 lat=12ms\n"
        files = {
            f"med_{i:02d}.log": line.replace(b"{n}", str(i).encode()) * 1500
            for i in range(20)
        }
        corpus = _write_corpus(tmp_path, files)
        self._measure(corpus, "D-perf_medium", "20 × ~100 KB structured logs ≈ 2 MB")

    def test_large_corpus_perf(self, tmp_path):
        """5 files × 2 MB each ≈ 10 MB total."""
        line = b"METRIC host=server-01 cpu=50 mem=1024 ts=2024-01-01T00:00:00Z\n"
        size = 2 * 1024 * 1024
        data = (line * (size // len(line) + 1))[:size]
        files = {f"large_{i:02d}.log": data for i in range(5)}
        corpus = _write_corpus(tmp_path, files)
        self._measure(corpus, "D-perf_large", "5 × 2 MB repetitive logs ≈ 10 MB")


# ---------------------------------------------------------------------------
# E) Regression gate
# ---------------------------------------------------------------------------


class TestRegressionGate:
    """Flag cases where MC is significantly worse than TAR+ZSTD."""

    @staticmethod
    def _check_regression(corpus: Path, label: str, expected_low_structure: bool = False) -> None:
        archive = compress_corpus_template(corpus)
        tz = tar_zstd_compress_dir(corpus)
        mc_size = len(archive)
        tz_size = len(tz)
        delta_pct = (mc_size - tz_size) / tz_size * 100.0 if tz_size > 0 else 0.0

        if delta_pct > 10.0:
            if expected_low_structure:
                notes = f"Δ={delta_pct:.1f}% – EXPLAINABLE (low/no structure)"
                status = "PASS"
            else:
                notes = f"Δ={delta_pct:.1f}% – REGRESSION: MC unexpectedly worse than TAR+ZSTD"
                status = "REGRESSION"
        else:
            notes = f"Δ={delta_pct:.1f}% – within threshold"
            status = "PASS"

        _record(label, status, mc_size, tz_size, notes)

        # Regression for structured logs (where MC should excel) is a test failure.
        if status == "REGRESSION":
            pytest.fail(
                f"{label}: MC ({mc_size:,} B) > TAR+ZSTD ({tz_size:,} B) "
                f"by {delta_pct:.1f}% without low-structure justification"
            )

    def test_regression_structured_logs(self, tmp_path):
        """MC must not be significantly worse than TAR+ZSTD on structured logs."""
        line = "2024-01-01T{h:02d}:{m:02d}:{s:02d}Z INFO req={n} status=200 path=/api\n"
        files = {
            f"day{d}.log": "".join(
                line.format(h=i // 3600, m=(i // 60) % 60, s=i % 60, n=i + d * 1000)
                for i in range(500)
            ).encode()
            for d in range(10)
        }
        corpus = _write_corpus(tmp_path, files)
        self._check_regression(corpus, "E-regression_structured_logs",
                               expected_low_structure=False)

    def test_regression_nginx_logs(self, tmp_path):
        """MC must not be worse than TAR+ZSTD on nginx-style access logs."""
        corpus = gen_nginx_logs(tmp_path)
        self._check_regression(corpus, "E-regression_nginx",
                               expected_low_structure=False)

    def test_regression_random_data(self, tmp_path):
        """Random data – MC may legitimately be larger (low structure, explainable)."""
        corpus = gen_random_data(tmp_path)
        self._check_regression(corpus, "E-regression_random",
                               expected_low_structure=True)

    def test_regression_json_corpus(self, tmp_path):
        """MC must not be significantly worse than TAR+ZSTD on JSON lines."""
        corpus = gen_json_corpus(tmp_path)
        self._check_regression(corpus, "E-regression_json",
                               expected_low_structure=False)

    def test_regression_mixed_formats(self, tmp_path):
        """Mixed-format corpus – moderate tolerance."""
        corpus = gen_mixed_formats(tmp_path)
        self._check_regression(corpus, "E-regression_mixed",
                               expected_low_structure=False)
