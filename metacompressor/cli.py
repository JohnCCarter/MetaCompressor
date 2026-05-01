"""CLI entry-point for MetaCompressor.

Commands
--------
mc compress  <input>  <output.mc1>
mc decompress <input.mc1> <output>
mc compare   <input>
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import zstandard as zstd

from metacompressor.compressor import compress
from metacompressor.decompressor import decompress


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def _write(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


def cmd_compress(args: argparse.Namespace) -> None:
    data = _read(args.input)
    mc1 = compress(data)
    _write(args.output, mc1)
    ratio = len(mc1) / len(data) if data else float("nan")
    print(f"Compressed {len(data):,} → {len(mc1):,} bytes  (ratio {ratio:.3f})")


def cmd_decompress(args: argparse.Namespace) -> None:
    mc1 = _read(args.input)
    original = decompress(mc1)
    _write(args.output, original)
    print(f"Decompressed {len(mc1):,} → {len(original):,} bytes")


def cmd_compare(args: argparse.Namespace) -> None:
    data = _read(args.input)
    original_size = len(data)

    # --- MetaCompressor ---
    t0 = time.perf_counter()
    mc1 = compress(data)
    mc_time = time.perf_counter() - t0
    mc_size = len(mc1)

    # verify round-trip
    reconstructed = decompress(mc1)
    if reconstructed != data:
        print("ERROR: MetaCompressor round-trip mismatch!", file=sys.stderr)
        sys.exit(1)

    # --- Zstandard baseline ---
    cctx = zstd.ZstdCompressor(level=3)
    t0 = time.perf_counter()
    zstd_data = cctx.compress(data)
    zstd_time = time.perf_counter() - t0
    zstd_size = len(zstd_data)

    def ratio(compressed: int) -> str:
        if original_size == 0:
            return "N/A"
        return f"{compressed / original_size:.4f}"

    print(f"File            : {args.input}")
    print(f"Original size   : {original_size:>12,} bytes")
    print(f"MC size         : {mc_size:>12,} bytes  ratio {ratio(mc_size)}  time {mc_time*1000:.1f} ms")
    print(f"ZSTD size       : {zstd_size:>12,} bytes  ratio {ratio(zstd_size)}  time {zstd_time*1000:.1f} ms")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mc",
        description="MetaCompressor – deterministic lossless compression",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_compress = sub.add_parser("compress", help="Compress a file to .mc1")
    p_compress.add_argument("input", help="Input file path")
    p_compress.add_argument("output", help="Output .mc1 file path")
    p_compress.set_defaults(func=cmd_compress)

    p_decompress = sub.add_parser("decompress", help="Decompress a .mc1 file")
    p_decompress.add_argument("input", help="Input .mc1 file path")
    p_decompress.add_argument("output", help="Output file path")
    p_decompress.set_defaults(func=cmd_decompress)

    p_compare = sub.add_parser("compare", help="Compare MC vs ZSTD compression")
    p_compare.add_argument("input", help="Input file path")
    p_compare.set_defaults(func=cmd_compare)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
