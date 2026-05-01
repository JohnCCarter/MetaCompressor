"""CLI entry-point for MetaCompressor.

Commands
--------
mc compress     <input>       <output.mc1>
mc decompress   <input.mc1>   <output>
mc compare      <input>
mc compress-dir <input_dir>   <output.mc1dir>
mc decompress-dir <input.mc1dir> <output_dir>
mc compare-dir  <input_dir>
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import zstandard as zstd

from metacompressor.compressor import compress
from metacompressor.decompressor import decompress
from metacompressor.corpus import compress_corpus, decompress_corpus


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


def cmd_compress_dir(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    output_path = Path(args.output)

    use_delta = not args.no_delta

    t0 = time.perf_counter()
    mc1dir = compress_corpus(input_dir, use_delta=use_delta)
    elapsed = time.perf_counter() - t0

    output_path.write_bytes(mc1dir)

    # Calculate total uncompressed size for reporting
    total_original = sum(
        p.stat().st_size for p in input_dir.rglob("*") if p.is_file()
    )
    ratio = len(mc1dir) / total_original if total_original else float("nan")
    delta_label = "" if use_delta else " (no delta)"
    print(
        f"Compressed {input_dir}  ({total_original:,} bytes across files){delta_label}\n"
        f"  → {output_path}  {len(mc1dir):,} bytes  ratio {ratio:.3f}  time {elapsed*1000:.1f} ms"
    )


def cmd_decompress_dir(args: argparse.Namespace) -> None:
    data = Path(args.input).read_bytes()
    output_dir = Path(args.output_dir)

    t0 = time.perf_counter()
    extracted = decompress_corpus(data, output_dir)
    elapsed = time.perf_counter() - t0

    total_out = sum(
        (output_dir / p).stat().st_size for p in extracted
    )
    print(
        f"Decompressed {len(data):,} bytes  → {len(extracted)} files  "
        f"({total_out:,} bytes total)  time {elapsed*1000:.1f} ms"
    )


def cmd_compare_dir(args: argparse.Namespace) -> None:
    """Compare MC corpus compression vs per-file ZSTD on a directory."""
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"ERROR: not a directory: {input_dir}", file=sys.stderr)
        sys.exit(1)

    all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    if not all_files:
        print("No files found in directory.", file=sys.stderr)
        sys.exit(1)

    # Read all file data once
    file_data = [(p.relative_to(input_dir).as_posix(), p.read_bytes()) for p in all_files]
    total_original = sum(len(d) for _, d in file_data)

    # --- MetaCompressor corpus ---
    t0 = time.perf_counter()
    mc1dir = compress_corpus(input_dir)
    mc_time = time.perf_counter() - t0
    mc_size = len(mc1dir)

    # --- Zstandard per-file (level 3) ---
    cctx = zstd.ZstdCompressor(level=3)
    t0 = time.perf_counter()
    zstd_total = sum(len(cctx.compress(d)) for _, d in file_data)
    zstd_time = time.perf_counter() - t0

    def ratio(compressed: int) -> str:
        if total_original == 0:
            return "N/A"
        return f"{compressed / total_original:.4f}"

    print(f"Directory       : {input_dir}")
    print(f"Files           : {len(all_files)}")
    print(f"Original size   : {total_original:>12,} bytes  (sum of all files)")
    print(
        f"MC corpus size  : {mc_size:>12,} bytes  ratio {ratio(mc_size)}"
        f"  time {mc_time*1000:.1f} ms"
    )
    print(
        f"ZSTD per-file   : {zstd_total:>12,} bytes  ratio {ratio(zstd_total)}"
        f"  time {zstd_time*1000:.1f} ms"
    )
    if total_original > 0:
        saving = zstd_total - mc_size
        pct = saving / zstd_total * 100 if zstd_total else 0
        sign = "+" if saving >= 0 else ""
        print(f"MC vs ZSTD      : {sign}{saving:,} bytes  ({sign}{pct:.1f}%)")


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

    p_compress_dir = sub.add_parser("compress-dir", help="Compress a directory to .mc1dir (corpus mode)")
    p_compress_dir.add_argument("input_dir", help="Input directory path")
    p_compress_dir.add_argument("output", help="Output .mc1dir file path")
    p_compress_dir.add_argument(
        "--no-delta",
        action="store_true",
        default=False,
        help="Disable intra-chunk delta encoding (store every unique chunk verbatim).",
    )
    p_compress_dir.set_defaults(func=cmd_compress_dir)

    p_decompress_dir = sub.add_parser("decompress-dir", help="Decompress a .mc1dir archive to a directory")
    p_decompress_dir.add_argument("input", help="Input .mc1dir file path")
    p_decompress_dir.add_argument("output_dir", help="Output directory path")
    p_decompress_dir.set_defaults(func=cmd_decompress_dir)

    p_compare_dir = sub.add_parser("compare-dir", help="Compare MC corpus vs per-file ZSTD on a directory")
    p_compare_dir.add_argument("input_dir", help="Input directory path")
    p_compare_dir.set_defaults(func=cmd_compare_dir)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
