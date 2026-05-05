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
import math
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

from metacompressor.container import (  # noqa: E402
    _ZSTD_LEVEL,
    MAGIC_DIR,
    VERSION_DIR,
    pack_mc1dir_payload_affinity,
    pack_mc1dir_payload_msgpack,
)
from metacompressor.corpus import build_corpus_container, compress_corpus  # noqa: E402

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
    "host",
    "port",
    "max_connections",
    "timeout_ms",
    "retry_count",
    "log_level",
    "enable_tls",
    "cert_path",
    "key_path",
    "ca_path",
    "database_url",
    "redis_url",
    "queue_url",
    "metrics_port",
    "debug",
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
        msg_tpl.replace("{ms}", str(_RNG.randint(1, 500)))
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
        entries.append(
            json.dumps(
                {
                    "timestamp": f"2024-01-15T{(ts + i) // 3600:02d}:{((ts + i) % 3600) // 60:02d}:{(ts + i) % 60:02d}Z",
                    "level": _RNG.choice(_LOG_LEVELS),
                    "service": _RNG.choice(_SERVICES),
                    "message": _RNG.choice(_MESSAGES)
                    .replace("{ms}", str(_RNG.randint(1, 500)))
                    .replace("{key}", _rand_key())
                    .replace("{n}", str(_RNG.randint(1, 5)))
                    .replace("{job}", f"job-{_RNG.randint(1000, 9999)}")
                    .replace("{ip}", _rand_ip()),
                    "request_id": f"{_rand_key(8)}-{_rand_key(4)}-{_rand_key(4)}",
                    "duration_ms": _RNG.randint(1, 2000),
                    "status_code": _RNG.choice([200, 200, 200, 201, 400, 404, 500]),
                }
            )
        )
    return "\n".join(entries).encode()


def _make_config(service_name: str) -> bytes:
    """Structured config file (JSON-like)."""
    cfg = {
        k: _RNG.choice([True, False, str(_RNG.randint(1, 9999)), _rand_key()])
        for k in _CONFIG_KEYS
    }
    cfg["service"] = service_name
    cfg["version"] = f"1.{_RNG.randint(0, 9)}.{_RNG.randint(0, 20)}"
    return json.dumps(cfg, indent=2).encode()


def generate_corpus(
    root: Path, num_log_files: int = 20, num_json_files: int = 10, num_configs: int = 5
) -> None:
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


def _zstd_compress(data: bytes) -> bytes:
    cctx = zstd.ZstdCompressor(level=3)
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


def measure_mc_corpus(corpus_dir: Path, use_delta: bool = True) -> tuple[int, float]:
    """MetaCompressor corpus mode (.mc1dir)."""
    t0 = time.perf_counter()
    mc_bytes = compress_corpus(corpus_dir, use_delta=use_delta)
    elapsed = time.perf_counter() - t0
    return len(mc_bytes), elapsed


def measure_mc_corpus_phased_msgpack(
    corpus_dir: Path, use_delta: bool = True
) -> tuple[int, float, float, float, float]:
    """Legacy all-msgpack payload: (archive_size, total_s, transform_s, pack_s, zstd_s)."""
    t0 = time.perf_counter()
    container = build_corpus_container(corpus_dir, use_delta=use_delta)
    t1 = time.perf_counter()
    raw = pack_mc1dir_payload_msgpack(container)
    t2 = time.perf_counter()
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    zstd_body = cctx.compress(raw)
    t3 = time.perf_counter()
    archive = MAGIC_DIR + bytes([VERSION_DIR]) + zstd_body
    return (
        len(archive),
        t3 - t0,
        t1 - t0,
        t2 - t1,
        t3 - t2,
    )


def measure_mc_corpus_phased(
    corpus_dir: Path, use_delta: bool = True
) -> tuple[int, float, float, float, float]:
    """ZSTD-affinity layout (MCZ1): (archive_size, total_s, transform_s, pack_s, zstd_s)."""
    t0 = time.perf_counter()
    container = build_corpus_container(corpus_dir, use_delta=use_delta)
    t1 = time.perf_counter()
    raw = pack_mc1dir_payload_affinity(container)
    t2 = time.perf_counter()
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    zstd_body = cctx.compress(raw)
    t3 = time.perf_counter()
    archive = MAGIC_DIR + bytes([VERSION_DIR]) + zstd_body
    return (
        len(archive),
        t3 - t0,
        t1 - t0,
        t2 - t1,
        t3 - t2,
    )


def measure_mc_corpus_twostream_zstd(
    corpus_dir: Path, use_delta: bool = True
) -> tuple[int, float, float, float, float]:
    """Experiment: ZSTD(region A) + ZSTD(region B) as separate frames.

    Region A = ``MCZ1`` header through end of raw chunk blob (chunk-first layout).
    Region B = msgpack metadata + binary delta tail.

    Returns ``(sum_compressed_bytes, pack_s, zstd_meta_s, zstd_blob_s, decode_verify_s)``.
    Decompress both frames, concatenate, and run :func:`unpack_mc1dir_payload`
    to verify (not a valid on-disk .mc1dir).
    """
    from metacompressor.zstd_affinity_pack_v1 import MCZ1_MAGIC, unpack_mc1dir_payload

    container = build_corpus_container(corpus_dir, use_delta=use_delta)
    t0 = time.perf_counter()
    raw = pack_mc1dir_payload_affinity(container)
    t1 = time.perf_counter()
    pack_s = t1 - t0
    if len(raw) < 9 or raw[:4] != MCZ1_MAGIC:
        raise RuntimeError("expected ZSTD-affinity payload")
    blob_len = int.from_bytes(raw[5:9], "little")
    blob_end = 9 + blob_len
    if blob_end > len(raw):
        raise RuntimeError("truncated affinity payload")
    chunk_region = raw[:blob_end]
    meta_delta_region = raw[blob_end:]
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    t2 = time.perf_counter()
    z_chunk = cctx.compress(chunk_region)
    t3 = time.perf_counter()
    z_meta = cctx.compress(meta_delta_region)
    t4 = time.perf_counter()

    dctx = zstd.ZstdDecompressor()
    t5 = time.perf_counter()
    raw_back = dctx.decompress(z_chunk) + dctx.decompress(z_meta)
    unpack_mc1dir_payload(raw_back)
    t6 = time.perf_counter()
    return (
        len(z_chunk) + len(z_meta),
        pack_s,
        t3 - t2,
        t4 - t3,
        t6 - t5,
    )


def measure_mc_corpus_decode(corpus_dir: Path, use_delta: bool = True) -> float:
    """Wall time to ``compress_corpus`` + ``decompress_corpus`` to a temp dir."""
    from metacompressor.corpus import compress_corpus, decompress_corpus

    t0 = time.perf_counter()
    archive = compress_corpus(corpus_dir, use_delta=use_delta)
    with tempfile.TemporaryDirectory() as td:
        decompress_corpus(archive, Path(td))
    return time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024**2:.2f} MB"


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

    print("  [1/4] ZSTD per file … ", end="", flush=True)
    zstd_size, zstd_time = measure_zstd_per_file(corpus_dir)
    print(f"{_fmt_size(zstd_size)}  ({zstd_time:.3f}s)")

    print("  [2/4] TAR + ZSTD … ", end="", flush=True)
    tar_size, tar_time = measure_tar_zstd(corpus_dir)
    print(f"{_fmt_size(tar_size)}  ({tar_time:.3f}s)")

    print("  [3/4] MC compress-dir (no delta) … ", end="", flush=True)
    mc_nd_size, mc_nd_time = measure_mc_corpus(corpus_dir, use_delta=False)
    print(f"{_fmt_size(mc_nd_size)}  ({mc_nd_time:.3f}s)")

    print("  [4/4] MC compress-dir (+ delta) … ", end="", flush=True)
    mc_delta_size, mc_delta_time = measure_mc_corpus(corpus_dir, use_delta=True)
    print(f"{_fmt_size(mc_delta_size)}  ({mc_delta_time:.3f}s)")

    (
        sz_mp,
        _tot_mp,
        transform_mp,
        pack_msgpack_s,
        zstd_msgpack_s,
    ) = measure_mc_corpus_phased_msgpack(corpus_dir, use_delta=True)
    (
        sz_aff,
        _tot_aff,
        transform_s,
        pack_affinity_s,
        zstd_mc_s,
    ) = measure_mc_corpus_phased(corpus_dir, use_delta=True)

    tw_sum_b, tw_pack_s, tw_zm, tw_zb, tw_dec_verify = measure_mc_corpus_twostream_zstd(
        corpus_dir, use_delta=True
    )
    decode_mc_s = measure_mc_corpus_decode(corpus_dir, use_delta=True)

    ratio_affinity_vs_msgpack_archive_pct = (
        (sz_aff - sz_mp) / sz_mp * 100.0 if sz_mp else 0.0
    )
    # Uncompressed payload Shannon entropy (0..8 bits/byte) — rough compressibility hint.
    container = build_corpus_container(corpus_dir, use_delta=True)
    raw_fp = pack_mc1dir_payload_msgpack(container)
    byte_freq = [0] * 256
    for b in raw_fp:
        byte_freq[b] += 1
    n = len(raw_fp)
    entropy_bits = 0.0
    if n:
        for c in byte_freq:
            if c:
                p = c / n
                entropy_bits -= p * math.log2(p)

    def ratio(compressed: int) -> str:
        return f"{raw_size / compressed:.2f}x"

    col_w = 32
    print()
    print("=" * 76)
    print(
        f"{'Method':<{col_w}} {'Size':>10} {'Ratio':>8} {'Time':>8}  Delta vs TAR+ZSTD"
    )
    print("-" * 76)
    print(
        f"{'Raw (uncompressed)':<{col_w}} {_fmt_size(raw_size):>10} {'1.00x':>8} {'—':>8}  —"
    )
    print(
        f"{'ZSTD per file':<{col_w}} {_fmt_size(zstd_size):>10} {ratio(zstd_size):>8} {zstd_time:>7.3f}s  {_fmt_delta(zstd_size, tar_size)}"
    )
    print(
        f"{'TAR + ZSTD (baseline)':<{col_w}} {_fmt_size(tar_size):>10} {ratio(tar_size):>8} {tar_time:>7.3f}s  (baseline)"
    )
    print(
        f"{'MC compress-dir (no delta)':<{col_w}} {_fmt_size(mc_nd_size):>10} {ratio(mc_nd_size):>8} {mc_nd_time:>7.3f}s  {_fmt_delta(mc_nd_size, tar_size)}"
    )
    print(
        f"{'MC compress-dir (+ delta)':<{col_w}} {_fmt_size(mc_delta_size):>10} {ratio(mc_delta_size):>8} {mc_delta_time:>7.3f}s  {_fmt_delta(mc_delta_size, tar_size)}"
    )
    print("=" * 76)

    print()
    print("--- Pack + ZSTD (MC + delta, same corpus) ---")
    print(
        f"  transform (chunk/dedupe/delta): {transform_s * 1000:>8.1f} ms\n"
        f"  legacy msgpack pack:            {pack_msgpack_s * 1000:>8.1f} ms  →  archive {_fmt_size(sz_mp)}\n"
        f"  ZSTD (msgpack payload):         {zstd_msgpack_s * 1000:>8.1f} ms\n"
        f"  ZSTD-affinity pack (experiment): {pack_affinity_s * 1000:>8.1f} ms  →  archive {_fmt_size(sz_aff)}\n"
        f"  ZSTD (affinity payload):        {zstd_mc_s * 1000:>8.1f} ms\n"
        f"  decode (compress+decompress):   {decode_mc_s * 1000:>8.1f} ms\n"
        f"  affinity archive vs msgpack:    {ratio_affinity_vs_msgpack_archive_pct:>+8.2f}%  (experimental)\n"
        f"  two-stream sum (experiment):    {_fmt_size(tw_sum_b)}  "
        f"(pack {tw_pack_s * 1000:.1f} ms, zstd_chunks {tw_zm * 1000:.1f} ms, "
        f"zstd_meta+delta {tw_zb * 1000:.1f} ms, verify {tw_dec_verify * 1000:.1f} ms)\n"
        f"  packed payload entropy:         {entropy_bits:>8.3f} bits/byte  ({n:,} B raw)"
    )

    delta_gain_bytes = mc_nd_size - mc_delta_size
    delta_gain_pct = delta_gain_bytes / mc_nd_size * 100 if mc_nd_size else 0.0

    print()
    print("--- Delta encoding impact ---")
    if delta_gain_bytes > 0:
        print(
            f"  Delta saves {_fmt_size(delta_gain_bytes)} ({delta_gain_pct:.1f}%) over MC (no delta)"
        )
    elif delta_gain_bytes < 0:
        print(
            f"  Delta adds {_fmt_size(-delta_gain_bytes)} ({-delta_gain_pct:.1f}%) overhead vs MC (no delta)"
        )
    else:
        print("  Delta had no effect on this corpus")

    print()
    if mc_delta_size < tar_size:
        saving_bytes = tar_size - mc_delta_size
        saving_pct = saving_bytes / tar_size * 100
        print("CORPUS_EDGE_FOUND")
        print(
            f"  MC (+ delta) is {_fmt_size(saving_bytes)} ({saving_pct:.1f}%) smaller than TAR+ZSTD"
        )
    else:
        overhead_bytes = mc_delta_size - tar_size
        overhead_pct = overhead_bytes / tar_size * 100
        print("NO_EDGE")
        print(
            f"  MC (+ delta) is {_fmt_size(overhead_bytes)} ({overhead_pct:.1f}%) LARGER than TAR+ZSTD.\n"
            f"  Explanation: The cross-file deduplication savings from the shared chunk\n"
            f"  dictionary do not outweigh the framing overhead on this corpus.  TAR+ZSTD\n"
            f"  benefits from compressing the full concatenated byte stream, which lets\n"
            f"  zstandard exploit cross-file repetition via its sliding window without\n"
            f"  the per-chunk dictionary layout paid by MC."
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark MetaCompressor corpus mode."
    )
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
