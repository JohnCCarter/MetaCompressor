"""CLI entry-point for MetaCompressor.

Commands
--------
mc compress      <input>      <output.mc1>  [--chunking fixed|cdc]
mc decompress    <input.mc1>  <output>
mc compare       <input>      [--chunking fixed|cdc]
mc compress-dir  <input_dir>  <output.mc1>  [--chunking fixed|cdc]
mc decompress-dir <input.mc1> <output_dir>
mc compare-dir   <input_dir>  [--chunking fixed|cdc]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import zstandard as zstd

from metacompressor.compressor import compress, compress_corpus, CHUNKING_FIXED, CHUNKING_CDC


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def _write(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


def _get_chunking(args: argparse.Namespace) -> str:
    return getattr(args, "chunking", CHUNKING_FIXED) or CHUNKING_FIXED


def _collect_corpus_files(input_dir: Path) -> list[tuple[str, bytes]]:
    """Walk *input_dir* and return sorted ``(relative_posix_path, bytes)`` pairs."""
    files: list[tuple[str, bytes]] = []
    for p in sorted(input_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(input_dir).as_posix()
            files.append((rel, p.read_bytes()))
    return files


def _safe_output_path(output_dir: Path, rel_path: str) -> Path:
    """Return the resolved output path, raising ``ValueError`` on path traversal."""
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe path in corpus file: {rel_path!r}")
    return output_dir / rel


def cmd_compress(args: argparse.Namespace) -> None:
    data = _read(args.input)
    mc1 = compress(data, chunking_mode=_get_chunking(args))
    _write(args.output, mc1)
    ratio = len(mc1) / len(data) if data else float("nan")
    print(f"Compressed {len(data):,} → {len(mc1):,} bytes  (ratio {ratio:.3f})")


def cmd_decompress(args: argparse.Namespace) -> None:
    from metacompressor.decompressor import decompress
    mc1 = _read(args.input)
    original = decompress(mc1)
    _write(args.output, original)
    print(f"Decompressed {len(mc1):,} → {len(original):,} bytes")


def cmd_compare(args: argparse.Namespace) -> None:
    from metacompressor.decompressor import decompress
    data = _read(args.input)
    original_size = len(data)
    chunking_mode = _get_chunking(args)

    # --- MetaCompressor ---
    t0 = time.perf_counter()
    mc1 = compress(data, chunking_mode=chunking_mode)
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
    print(f"Chunking mode   : {chunking_mode}")
    print(f"Original size   : {original_size:>12,} bytes")
    print(f"MC size         : {mc_size:>12,} bytes  ratio {ratio(mc_size)}  time {mc_time*1000:.1f} ms")
    print(f"ZSTD size       : {zstd_size:>12,} bytes  ratio {ratio(zstd_size)}  time {zstd_time*1000:.1f} ms")


def cmd_compress_dir(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"ERROR: {args.input_dir!r} is not a directory", file=sys.stderr)
        sys.exit(1)

    files = _collect_corpus_files(input_dir)
    if not files:
        print(f"WARNING: No files found in {args.input_dir!r}", file=sys.stderr)

    chunking_mode = _get_chunking(args)
    mc1 = compress_corpus(files, chunking_mode=chunking_mode)
    _write(args.output, mc1)

    total = sum(len(d) for _, d in files)
    ratio = len(mc1) / total if total else float("nan")
    print(
        f"Compressed {len(files)} files, {total:,} → {len(mc1):,} bytes"
        f"  (ratio {ratio:.3f})"
    )


def cmd_decompress_dir(args: argparse.Namespace) -> None:
    from metacompressor.decompressor import decompress_corpus

    mc1 = _read(args.input)
    files = decompress_corpus(mc1)

    out_dir = Path(args.output_dir)
    for rel_path, data in files:
        out_path = _safe_output_path(out_dir, rel_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

    total = sum(len(d) for _, d in files)
    print(f"Decompressed {len(files)} files, {len(mc1):,} → {total:,} bytes")


def cmd_compare_dir(args: argparse.Namespace) -> None:
    from metacompressor.decompressor import decompress_corpus

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"ERROR: {args.input_dir!r} is not a directory", file=sys.stderr)
        sys.exit(1)

    files = _collect_corpus_files(input_dir)
    if not files:
        print("No files found.")
        return

    chunking_mode = _get_chunking(args)
    total_size = sum(len(d) for _, d in files)
    # Concatenation order is deterministic (files already sorted by path).
    all_data = b"".join(d for _, d in files)

    # --- MetaCompressor corpus ---
    t0 = time.perf_counter()
    mc1 = compress_corpus(files, chunking_mode=chunking_mode)
    mc_time = time.perf_counter() - t0
    mc_size = len(mc1)

    # verify round-trip
    restored = decompress_corpus(mc1)
    restored_map = dict(restored)
    for path, data in files:
        if restored_map.get(path) != data:
            print(f"ERROR: round-trip mismatch for {path!r}", file=sys.stderr)
            sys.exit(1)

    # --- Zstandard baseline (concatenated) ---
    cctx = zstd.ZstdCompressor(level=3)
    t0 = time.perf_counter()
    zstd_data = cctx.compress(all_data)
    zstd_time = time.perf_counter() - t0
    zstd_size = len(zstd_data)

    def ratio(compressed: int) -> str:
        if total_size == 0:
            return "N/A"
        return f"{compressed / total_size:.4f}"

    print(f"Directory       : {args.input_dir}")
    print(f"Files           : {len(files)}")
    print(f"Chunking mode   : {chunking_mode}")
    print(f"Original size   : {total_size:>12,} bytes")
    print(f"MC corpus size  : {mc_size:>12,} bytes  ratio {ratio(mc_size)}  time {mc_time*1000:.1f} ms")
    print(f"ZSTD (concat)   : {zstd_size:>12,} bytes  ratio {ratio(zstd_size)}  time {zstd_time*1000:.1f} ms")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mc",
        description="MetaCompressor – deterministic lossless compression",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_compress = sub.add_parser("compress", help="Compress a file to .mc1")
    p_compress.add_argument("input", help="Input file path")
    p_compress.add_argument("output", help="Output .mc1 file path")
    p_compress.add_argument(
        "--chunking",
        choices=[CHUNKING_FIXED, CHUNKING_CDC],
        default=CHUNKING_FIXED,
        help="Chunking mode: 'fixed' (default) or 'cdc'",
    )
    p_compress.set_defaults(func=cmd_compress)

    p_decompress = sub.add_parser("decompress", help="Decompress a .mc1 file")
    p_decompress.add_argument("input", help="Input .mc1 file path")
    p_decompress.add_argument("output", help="Output file path")
    p_decompress.set_defaults(func=cmd_decompress)

    p_compare = sub.add_parser("compare", help="Compare MC vs ZSTD compression")
    p_compare.add_argument("input", help="Input file path")
    p_compare.add_argument(
        "--chunking",
        choices=[CHUNKING_FIXED, CHUNKING_CDC],
        default=CHUNKING_FIXED,
        help="Chunking mode: 'fixed' (default) or 'cdc'",
    )
    p_compare.set_defaults(func=cmd_compare)

    p_compress_dir = sub.add_parser(
        "compress-dir", help="Compress a directory to a corpus .mc1 file"
    )
    p_compress_dir.add_argument("input_dir", help="Input directory path")
    p_compress_dir.add_argument("output", help="Output corpus .mc1 file path")
    p_compress_dir.add_argument(
        "--chunking",
        choices=[CHUNKING_FIXED, CHUNKING_CDC],
        default=CHUNKING_FIXED,
        help="Chunking mode: 'fixed' (default) or 'cdc'",
    )
    p_compress_dir.set_defaults(func=cmd_compress_dir)

    p_decompress_dir = sub.add_parser(
        "decompress-dir", help="Extract a corpus .mc1 file to a directory"
    )
    p_decompress_dir.add_argument("input", help="Input corpus .mc1 file path")
    p_decompress_dir.add_argument("output_dir", help="Output directory path")
    p_decompress_dir.set_defaults(func=cmd_decompress_dir)

    p_compare_dir = sub.add_parser(
        "compare-dir", help="Benchmark MC corpus vs ZSTD on a directory"
    )
    p_compare_dir.add_argument("input_dir", help="Input directory path")
    p_compare_dir.add_argument(
        "--chunking",
        choices=[CHUNKING_FIXED, CHUNKING_CDC],
        default=CHUNKING_FIXED,
        help="Chunking mode: 'fixed' (default) or 'cdc'",
    )
    p_compare_dir.set_defaults(func=cmd_compare_dir)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
