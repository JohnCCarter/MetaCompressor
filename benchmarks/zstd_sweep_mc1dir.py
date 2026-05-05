"""ZSTD level / window sweep for .mc1dir msgpack payload (benchmark / report).

Usage (from repo root)::

    python3 benchmarks/zstd_sweep_mc1dir.py [--corpus-dir DIR]

Uses the same synthetic corpus generator as ``bench_corpus.py`` when no
``--corpus-dir`` is given.  Measures **uncompressed** short-key msgpack build
time, ZSTD time, and archive size (``MCD\\x00`` + version + zstd body) for
levels 1–3 and optional ``window_log`` overrides.

Does not change library defaults; for reporting only.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import zstandard as zstd

sys.path.insert(0, str(Path(__file__).parent.parent))

# Reuse corpus generator from bench_corpus
from benchmarks.bench_corpus import generate_corpus  # noqa: E402
from metacompressor.container import (  # noqa: E402
    MAGIC_DIR,
    pack_mc1dir_payload,
)
from metacompressor.corpus import build_corpus_container  # noqa: E402


def _archive_size_zstd(
    raw_payload: bytes, level: int, window_log: int | None
) -> tuple[int, float]:
    t0 = time.perf_counter()
    if window_log is None:
        cctx = zstd.ZstdCompressor(level=level)
    else:
        params = zstd.ZstdCompressionParameters(
            compression_level=level, window_log=window_log
        )
        cctx = zstd.ZstdCompressor(compression_params=params)
    body = cctx.compress(raw_payload)
    elapsed = time.perf_counter() - t0
    return len(MAGIC_DIR) + 1 + len(body), elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="ZSTD sweep on MC .mc1dir payload.")
    parser.add_argument("--corpus-dir", default=None)
    args = parser.parse_args()

    if args.corpus_dir:
        corpus = Path(args.corpus_dir)
        corpus.mkdir(parents=True, exist_ok=True)
    else:
        tmp = tempfile.mkdtemp(prefix="mc_zstd_sweep_")
        corpus = Path(tmp)
        generate_corpus(corpus)

    container = build_corpus_container(corpus, use_delta=True)
    t0 = time.perf_counter()
    raw = pack_mc1dir_payload(container)
    pack_s = time.perf_counter() - t0

    baseline_size, baseline_zstd = _archive_size_zstd(raw, 3, None)

    print("MC .mc1dir ZSTD sweep (short-key msgpack payload)")
    print(f"  uncompressed payload: {len(raw):,} B")
    print(f"  pack time (short msgpack): {pack_s * 1000:.2f} ms")
    print()
    print(
        f"{'config':<28} {'archive':>10} {'Δ vs L3':>10} {'zstd_ms':>10} {'ratio*':>8}"
    )
    print("-" * 70)

    def pct_delta(sz: int) -> str:
        d = (sz - baseline_size) / baseline_size * 100 if baseline_size else 0.0
        return f"{d:+.2f}%"

    for level in (1, 2, 3):
        sz, zt = _archive_size_zstd(raw, level, None)
        print(
            f"{f'level={level}':<28} {sz:>10,} {pct_delta(sz):>10} {zt * 1000:>10.2f}  {'—':>8}"
        )

    for wlog in (20, 22, 24, 27):
        try:
            sz, zt = _archive_size_zstd(raw, 3, wlog)
        except Exception as exc:
            print(
                f"{'level=3 winlog=' + str(wlog):<28} {'(skip)':>10} {'':>10} {str(exc)[:20]:>10}"
            )
            continue
        print(
            f"{f'level=3 winlog={wlog}':<28} {sz:>10,} {pct_delta(sz):>10} {zt * 1000:>10.2f}  {'—':>8}"
        )

    print()
    print("* ratio column reserved; compare archive sizes to baseline level=3.")
    print(f"  baseline archive (level 3): {baseline_size:,} B")


if __name__ == "__main__":
    main()
