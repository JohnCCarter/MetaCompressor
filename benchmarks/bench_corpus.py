"""Corpus-mode benchmark: MetaCompressor vs ZSTD-per-file vs TAR+ZSTD.

Usage
-----
    python benchmarks/bench_corpus.py [--corpus-dir DIR] [--keep]

Generates a synthetic but realistic corpus (log files, JSON logs, config
files), then measures:

    1. ZSTD per file  (each file compressed independently)
    2. TAR + ZSTD     (deterministic tar; sorted files, mtime=0, no uid/gid)
    3. MC compress-dir (.mc1dir via metacompressor.corpus.compress_corpus)

Prints a summary table and exits with the verdict:

    CORPUS_EDGE_FOUND   – if MC < TAR+ZSTD
    NO_EDGE             – otherwise, with explanation
"""

from __future__ import annotations

import argparse
import io
import json
import random
import string
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import zstandard as zstd

# Add repo root to path so the script works when run from any directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from metacompressor.corpus import compress_corpus  # noqa: E402

# ---------------------------------------------------------------------------
# Corpus generation
# ---------------------------------------------------------------------------

_RNG = random.Random(42)

_LOG_LEVELS = ["INFO", "WARN", "ERROR", "DEBUG"]
_SERVICES = ["auth-service", "api-gateway", "db-proxy", "cache-layer", "worker"]
_MESSAGES = [
    "Request received from client",
    "Database query executed in {ms}ms",
    "Cache miss for key '{key}'",
    "Token validation succeeded",
    "Connection pool exhausted, waiting for slot",
    "Retry attempt {n} for job {job}",
    "Health check passed",
    "Shutting down gracefully",
    "Configuration reloaded",
    "Rate limit exceeded for IP {ip}",
]

_CONFIG_KEYS = [
    "host", "port", "max_connections", "timeout_ms", "retry_count",
    "log_level", "enable_tls", "cert_path", "key_path", "ca_path",
    "database_url", "redis_url", "queue_url", "metrics_port", "debug",
]


def _rand_ip() -> str:
    return ".".join(str(_RNG.randint(1, 254)) for _ in range(4))


def _rand_key(length: int = 8) -> str:
    return "".join(_RNG.choices(string.ascii_lowercase, k=length))


def _make_log_line(ts_sec: int, line_num: int) -> str:
    level = _RNG.choice(_LOG_LEVELS)
    service = _RNG.choice(_SERVICES)
    msg_tpl = _RNG.choice(_MESSAGES)
    msg = (
        msg_tpl
        .replace("{ms}", str(_RNG.randint(1, 500)))
        .replace("{key}", _rand_key())
        .replace("{n}", str(_RNG.randint(1, 5)))
        .replace("{job}", f"job-{_RNG.randint(1000, 9999)}")
        .replace("{ip}", _rand_ip())
    )
    ts = f"2024-01-15T{ts_sec // 3600:02d}:{(ts_sec % 3600) // 60:02d}:{ts_sec % 60:02d}Z"
    return f"{ts} [{level}] {service}: {msg} (line={line_num})\n"


def _make_text_log(lines: int = 500) -> bytes:
    """Plain-text log file with realistic, repetitive structure."""
    ts = _RNG.randint(0, 86399 - lines)
    parts = [_make_log_line(ts + i, i) for i in range(lines)]
    return "".join(parts).encode()


def _make_json_log(records: int = 200) -> bytes:
    """Newline-delimited JSON log."""
    entries = []
    ts = _RNG.randint(0, 86399 - records)
    for i in range(records):
        entries.append(json.dumps({
            "timestamp": f"2024-01-15T{(ts + i) // 3600:02d}:{((ts + i) % 3600) // 60:02d}:{(ts + i) % 60:02d}Z",
            "level": _RNG.choice(_LOG_LEVELS),
            "service": _RNG.choice(_SERVICES),
            "message": _RNG.choice(_MESSAGES).replace("{ms}", str(_RNG.randint(1, 500)))
                                              .replace("{key}", _rand_key())
                                              .replace("{n}", str(_RNG.randint(1, 5)))
                                              .replace("{job}", f"job-{_RNG.randint(1000, 9999)}")
                                              .replace("{ip}", _rand_ip()),
            "request_id": f"{_rand_key(8)}-{_rand_key(4)}-{_rand_key(4)}",
            "duration_ms": _RNG.randint(1, 2000),
            "status_code": _RNG.choice([200, 200, 200, 201, 400, 404, 500]),
        }))
    return "\n".join(entries).encode()


def _make_config(service_name: str) -> bytes:
    """Structured config file (JSON-like)."""
    cfg = {k: _RNG.choice([True, False, str(_RNG.randint(1, 9999)), _rand_key()])
           for k in _CONFIG_KEYS}
    cfg["service"] = service_name
    cfg["version"] = f"1.{_RNG.randint(0, 9)}.{_RNG.randint(0, 20)}"
    return json.dumps(cfg, indent=2).encode()


def generate_corpus(root: Path, num_log_files: int = 20, num_json_files: int = 10,
                    num_configs: int = 5) -> None:
    """Write a realistic corpus under *root*."""
    logs_dir = root / "logs"
    json_dir = root / "json_logs"
    cfg_dir = root / "configs"
    for d in (logs_dir, json_dir, cfg_dir):
        d.mkdir(parents=True, exist_ok=True)

    for i in range(num_log_files):
        (logs_dir / f"service_{i:03d}.log").write_bytes(_make_text_log(500))

    for i in range(num_json_files):
        (json_dir / f"events_{i:03d}.jsonl").write_bytes(_make_json_log(200))

    for svc in _SERVICES[:num_configs]:
        (cfg_dir / f"{svc}.json").write_bytes(_make_config(svc))


# ---------------------------------------------------------------------------
# Compression helpers
# ---------------------------------------------------------------------------

_ZSTD_LEVEL = 3


def _zstd_compress(data: bytes) -> bytes:
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    return cctx.compress(data)


def measure_zstd_per_file(corpus_dir: Path) -> tuple[int, float]:
    """Sum of independently ZSTD-compressed files (no archive overhead)."""
    all_files = sorted(p for p in corpus_dir.rglob("*") if p.is_file())
    t0 = time.perf_counter()
    total = sum(len(_zstd_compress(p.read_bytes())) for p in all_files)
    elapsed = time.perf_counter() - t0
    return total, elapsed


def measure_tar_zstd(corpus_dir: Path) -> tuple[int, float]:
    """Deterministic TAR then ZSTD: sorted files, mtime=0, uid/gid=0."""
    all_files = sorted(p for p in corpus_dir.rglob("*") if p.is_file())

    t0 = time.perf_counter()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|") as tf:
        for fpath in all_files:
            data = fpath.read_bytes()
            info = tarfile.TarInfo(name=fpath.relative_to(corpus_dir).as_posix())
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tf.addfile(info, io.BytesIO(data))

    tar_bytes = buf.getvalue()
    compressed = _zstd_compress(tar_bytes)
    elapsed = time.perf_counter() - t0
    return len(compressed), elapsed


def measure_mc_corpus(corpus_dir: Path) -> tuple[int, float]:
    """MetaCompressor corpus mode (.mc1dir)."""
    t0 = time.perf_counter()
    mc_bytes = compress_corpus(corpus_dir)
    elapsed = time.perf_counter() - t0
    return len(mc_bytes), elapsed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.2f} MB"


def _fmt_delta(mc: int, baseline: int) -> str:
    diff = mc - baseline
    if baseline == 0:
        return f"{_fmt_size(diff)} (N/A)"
    pct = diff / baseline * 100
    sign = "+" if diff > 0 else ""
    return f"{sign}{_fmt_size(diff)} ({sign}{pct:.1f}%)"


def run_benchmark(corpus_dir: Path) -> None:
    print("\n=== Generating corpus ===")
    generate_corpus(corpus_dir)
    raw_size = sum(p.stat().st_size for p in corpus_dir.rglob("*") if p.is_file())
    num_files = sum(1 for p in corpus_dir.rglob("*") if p.is_file())
    print(f"  Files: {num_files}   Raw size: {_fmt_size(raw_size)}")

    print("\n=== Compressing ===")

    print("  [1/3] ZSTD per file … ", end="", flush=True)
    zstd_size, zstd_time = measure_zstd_per_file(corpus_dir)
    print(f"{_fmt_size(zstd_size)}  ({zstd_time:.3f}s)")

    print("  [2/3] TAR + ZSTD … ", end="", flush=True)
    tar_size, tar_time = measure_tar_zstd(corpus_dir)
    print(f"{_fmt_size(tar_size)}  ({tar_time:.3f}s)")

    print("  [3/3] MC compress-dir (.mc1dir) … ", end="", flush=True)
    mc_size, mc_time = measure_mc_corpus(corpus_dir)
    print(f"{_fmt_size(mc_size)}  ({mc_time:.3f}s)")

    def ratio(compressed: int) -> str:
        return f"{raw_size / compressed:.2f}x"

    col_w = 28
    print()
    print("=" * 68)
    print(f"{'Method':<{col_w}} {'Size':>10} {'Ratio':>8} {'Time':>8}  Delta vs TAR+ZSTD")
    print("-" * 68)
    print(f"{'Raw (uncompressed)':<{col_w}} {_fmt_size(raw_size):>10} {'1.00x':>8} {'—':>8}  —")
    print(f"{'ZSTD per file':<{col_w}} {_fmt_size(zstd_size):>10} {ratio(zstd_size):>8} {zstd_time:>7.3f}s  {_fmt_delta(zstd_size, tar_size)}")
    print(f"{'TAR + ZSTD (baseline)':<{col_w}} {_fmt_size(tar_size):>10} {ratio(tar_size):>8} {tar_time:>7.3f}s  (baseline)")
    print(f"{'MC compress-dir':<{col_w}} {_fmt_size(mc_size):>10} {ratio(mc_size):>8} {mc_time:>7.3f}s  {_fmt_delta(mc_size, tar_size)}")
    print("=" * 68)

    print()
    if mc_size < tar_size:
        saving_bytes = tar_size - mc_size
        saving_pct = saving_bytes / tar_size * 100
        print(f"CORPUS_EDGE_FOUND")
        print(f"  MC is {_fmt_size(saving_bytes)} ({saving_pct:.1f}%) smaller than TAR+ZSTD")
    else:
        overhead_bytes = mc_size - tar_size
        overhead_pct = overhead_bytes / tar_size * 100
        print(f"NO_EDGE")
        print(
            f"  MC is {_fmt_size(overhead_bytes)} ({overhead_pct:.1f}%) LARGER than TAR+ZSTD.\n"
            f"  Explanation: The cross-file deduplication savings from the shared chunk\n"
            f"  dictionary do not outweigh the overhead of the msgpack container and\n"
            f"  chunk-boundary fragmentation on this corpus.  TAR+ZSTD benefits from\n"
            f"  compressing the full concatenated byte stream, which lets zstandard\n"
            f"  exploit cross-file repetition via its sliding window without the\n"
            f"  per-chunk framing cost paid by MC."
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark MetaCompressor corpus mode.")
    parser.add_argument(
        "--corpus-dir",
        default=None,
        help="Directory to write the synthetic corpus into (default: tmp dir).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the corpus directory after the benchmark.",
    )
    args = parser.parse_args()

    if args.corpus_dir:
        corpus_path = Path(args.corpus_dir)
        corpus_path.mkdir(parents=True, exist_ok=True)
        run_benchmark(corpus_path)
    else:
        with tempfile.TemporaryDirectory(prefix="mc_bench_") as tmp:
            run_benchmark(Path(tmp))
            if args.keep:
                import shutil
                kept = Path(tempfile.mkdtemp(prefix="mc_bench_kept_"))
                shutil.copytree(tmp, str(kept / "corpus"))
                print(f"  Corpus kept at: {kept / 'corpus'}")


if __name__ == "__main__":
    main()
