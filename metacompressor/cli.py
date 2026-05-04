"""CLI entry-point for MetaCompressor.

Commands
--------
mc compress                <input>           <output.mc1>  [--chunking fixed|cdc]
mc decompress              <input.mc1>       <output>
mc compare                 <input>           [--chunking fixed|cdc]
mc compress-dir            <input_dir>       <output.mc1dir>
mc decompress-dir          <input.mc1dir>    <output_dir>
mc compress-template-dir   <input_dir>       <output.mck>
mc decompress-template-dir <input.mck>       <output_dir>
mc compare-dir             <input_dir>
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
import time
from pathlib import Path

import zstandard as zstd

from metacompressor.compressor import CHUNKING_CDC, CHUNKING_FIXED, compress
from metacompressor.corpus import compress_corpus, decompress_corpus
from metacompressor.corpus_template import (
    compress_corpus_template,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)
from metacompressor.decompressor import decompress
from metacompressor.log_template import compress_log


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def _write(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


def _get_chunking(args: argparse.Namespace) -> str:
    return getattr(args, "chunking", CHUNKING_FIXED) or CHUNKING_FIXED


def cmd_compress(args: argparse.Namespace) -> None:
    data = _read(args.input)
    mc1 = compress(data, chunking_mode=_get_chunking(args))
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
    print(
        f"MC size         : {mc_size:>12,} bytes  ratio {ratio(mc_size)}  time {mc_time * 1000:.1f} ms"
    )
    print(
        f"ZSTD size       : {zstd_size:>12,} bytes  ratio {ratio(zstd_size)}  time {zstd_time * 1000:.1f} ms"
    )


def cmd_compress_dir(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    output_path = Path(args.output)

    use_delta = not args.no_delta

    t0 = time.perf_counter()
    mc1dir = compress_corpus(input_dir, use_delta=use_delta)
    elapsed = time.perf_counter() - t0

    output_path.write_bytes(mc1dir)

    # Calculate total uncompressed size for reporting
    total_original = sum(p.stat().st_size for p in input_dir.rglob("*") if p.is_file())
    ratio = len(mc1dir) / total_original if total_original else float("nan")
    delta_label = "" if use_delta else " (no delta)"
    print(
        f"Compressed {input_dir}  ({total_original:,} bytes across files){delta_label}\n"
        f"  → {output_path}  {len(mc1dir):,} bytes  ratio {ratio:.3f}  time {elapsed * 1000:.1f} ms"
    )


def cmd_decompress_dir(args: argparse.Namespace) -> None:
    data = Path(args.input).read_bytes()
    output_dir = Path(args.output_dir)

    t0 = time.perf_counter()
    extracted = decompress_corpus(data, output_dir)
    elapsed = time.perf_counter() - t0

    total_out = sum((output_dir / p).stat().st_size for p in extracted)
    print(
        f"Decompressed {len(data):,} bytes  → {len(extracted)} files  "
        f"({total_out:,} bytes total)  time {elapsed * 1000:.1f} ms"
    )


def cmd_compress_template_dir(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    output_path = Path(args.output)

    t0 = time.perf_counter()
    mck = compress_corpus_template(input_dir)
    elapsed = time.perf_counter() - t0

    output_path.write_bytes(mck)

    total_original = sum(p.stat().st_size for p in input_dir.rglob("*") if p.is_file())
    ratio = len(mck) / total_original if total_original else float("nan")
    print(
        f"Compressed (template-dir) {input_dir}  ({total_original:,} bytes across files)\n"
        f"  → {output_path}  {len(mck):,} bytes  ratio {ratio:.3f}  time {elapsed * 1000:.1f} ms"
    )


def cmd_decompress_template_dir(args: argparse.Namespace) -> None:
    data = Path(args.input).read_bytes()
    output_dir = Path(args.output_dir)

    t0 = time.perf_counter()
    extracted = decompress_corpus_template(data, output_dir)
    elapsed = time.perf_counter() - t0

    total_out = sum((output_dir / p).stat().st_size for p in extracted)
    print(
        f"Decompressed (template-dir) {len(data):,} bytes  → {len(extracted)} files  "
        f"({total_out:,} bytes total)  time {elapsed * 1000:.1f} ms"
    )


def _tar_zstd_size(file_data: list) -> int:
    """Return the size of a TAR+ZSTD (level 3) archive of *file_data*.

    Parameters
    ----------
    file_data:
        List of ``(relative_path, data)`` tuples where *relative_path* is a
        POSIX string and *data* is the raw file bytes.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel_path, data in file_data:
            info = tarfile.TarInfo(name=rel_path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    tar_bytes = buf.getvalue()
    cctx = zstd.ZstdCompressor(level=3)
    return len(cctx.compress(tar_bytes))


def format_delta(mc_size: int, baseline_size: int, baseline_label: str) -> str:
    """Return a human-readable delta line comparing *mc_size* to *baseline_size*.

    Examples
    --------
    MC corpus-template is 20,898 bytes (11.1%) SMALLER than TAR+ZSTD.
    MC corpus-template is 5,200 bytes (2.8%) LARGER than TAR+ZSTD.
    MC corpus-template is equal in size to TAR+ZSTD.
    """
    delta = mc_size - baseline_size
    if baseline_size == 0:
        return "(baseline size is 0, delta not meaningful)"
    pct = abs(delta) / baseline_size * 100
    if delta < 0:
        return f"MC corpus-template is {abs(delta):,} bytes ({pct:.1f}%) SMALLER than {baseline_label}."
    if delta > 0:
        return f"MC corpus-template is {delta:,} bytes ({pct:.1f}%) LARGER than {baseline_label}."
    return f"MC corpus-template is equal in size to {baseline_label}."


def cmd_compare_dir(args: argparse.Namespace) -> None:
    """Compare MC corpus / corpus-template / per-file ZSTD / TAR+ZSTD on a directory."""
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"ERROR: not a directory: {input_dir}", file=sys.stderr)
        sys.exit(1)

    all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    if not all_files:
        print("No files found in directory.", file=sys.stderr)
        sys.exit(1)

    # Read all file data once
    file_data = [
        (p.relative_to(input_dir).as_posix(), p.read_bytes()) for p in all_files
    ]
    total_original = sum(len(d) for _, d in file_data)

    # --- MetaCompressor corpus ---
    t0 = time.perf_counter()
    mc1dir = compress_corpus(input_dir)
    mc_time = time.perf_counter() - t0
    mc_size = len(mc1dir)

    # --- Corpus template (shared template dictionary + metrics) ---
    mck, metrics = compress_corpus_template_with_metrics(input_dir)
    mck_size = len(mck)
    mck_timing = metrics["timing"]
    row_mode_size = metrics["row_mode_size"]
    columnar_size = metrics["columnar_size"]

    # --- Zstandard per-file (level 3) ---
    cctx = zstd.ZstdCompressor(level=3)
    t0 = time.perf_counter()
    zstd_total = sum(len(cctx.compress(d)) for _, d in file_data)
    zstd_time = time.perf_counter() - t0

    # --- Template mode per-file ---
    t0 = time.perf_counter()
    template_total = sum(len(compress_log(d)) for _, d in file_data)
    template_time = time.perf_counter() - t0

    # --- TAR + ZSTD ---
    t0 = time.perf_counter()
    tar_zstd = _tar_zstd_size(file_data)
    tar_zstd_time = time.perf_counter() - t0

    def ratio(compressed: int) -> str:
        if total_original == 0:
            return "N/A"
        return f"{compressed / total_original:.4f}"

    print(f"Directory            : {input_dir}")
    print(f"Files                : {len(all_files)}")
    print(f"Original size        : {total_original:>12,} bytes  (sum of all files)")
    print(
        f"MC corpus            : {mc_size:>12,} bytes  ratio {ratio(mc_size)}"
        f"  time {mc_time * 1000:.1f} ms"
    )
    print(
        f"MC template final    : {mck_size:>12,} bytes  ratio {ratio(mck_size)}"
        f"  time {mck_timing['total_s'] * 1000:.1f} ms"
    )
    print(
        f"MC template row      : {row_mode_size:>12,} bytes  ratio {ratio(row_mode_size)}"
    )
    print(
        f"MC template columnar : {columnar_size:>12,} bytes  ratio {ratio(columnar_size)}"
    )
    print(
        f"ZSTD per-file        : {zstd_total:>12,} bytes  ratio {ratio(zstd_total)}"
        f"  time {zstd_time * 1000:.1f} ms"
    )
    print(
        f"Template per-file    : {template_total:>12,} bytes  ratio {ratio(template_total)}"
        f"  time {template_time * 1000:.1f} ms"
    )
    print(
        f"TAR+ZSTD             : {tar_zstd:>12,} bytes  ratio {ratio(tar_zstd)}"
        f"  time {tar_zstd_time * 1000:.1f} ms"
    )

    print()
    print("--- Delta (MC template final vs baselines) ---")
    print(format_delta(mck_size, tar_zstd, "TAR+ZSTD"))
    print(format_delta(mck_size, zstd_total, "ZSTD per-file"))

    print()
    print("--- Corpus-template timing breakdown ---")
    print(f"  Template extraction : {mck_timing['extract_s'] * 1000:>8.1f} ms")
    print(f"  Serialisation       : {mck_timing['serialize_s'] * 1000:>8.1f} ms")
    print(f"  Zstd compression    : {mck_timing['zstd_s'] * 1000:>8.1f} ms")
    print(f"  Total               : {mck_timing['total_s'] * 1000:>8.1f} ms")

    print()
    print("--- Corpus-template explainability ---")
    print(f"  Structure v2 enabled : {metrics['structure_v2_enabled']}")
    print(f"  Files               : {metrics['num_files']}")
    print(f"  Lines               : {metrics['num_lines']:,}")
    print(f"  Shared templates    : {metrics['num_shared_templates']:,}")
    print(f"  JSON lines detected : {metrics['json_lines_detected']:,}")
    print(f"  JSON template count : {metrics['json_template_count']:,}")
    print(f"  Normalized templates: {metrics['normalized_template_count']:,}")
    print(f"  Fuzzy merges        : {metrics['fuzzy_merge_count']:,}")
    print(f"  Template reuse count: {metrics['template_reuse_count']:,}")
    print(f"  Template reuse rate : {metrics['template_reuse_rate'] * 100:.1f}%")
    print(f"  Reuse before        : {metrics['template_reuse_before'] * 100:.1f}%")
    print(f"  Reuse after         : {metrics['template_reuse_after'] * 100:.1f}%")
    print(f"  Raw fallback lines  : {metrics['raw_fallback_lines']:,}")
    print(f"  Binary fallback files:{metrics['binary_fallback_files']}")
    print(f"  Fallback reasons    : {metrics['fallback_reason_counts']}")
    print(f"  Avg vars/tpl line   : {metrics['avg_vars_per_tpl_line']:.2f}")
    print(f"  Columnar enabled    : {metrics['columnar_enabled']}")
    print(f"  Columnar templates  : {metrics['num_columnar_templates']:,}")
    print(f"  Encoded columns     : {metrics['num_encoded_columns']:,}")
    print(f"  Raw column fallback : {metrics['raw_column_fallback_count']:,}")
    print(f"  Column encodings    : {metrics['column_encoding_counts']}")
    print(f"  Columnar size       : {columnar_size:,}")
    print(f"  Row mode size       : {row_mode_size:,}")
    print(f"  Columnar vs row     : {metrics['columnar_savings_vs_row']:,} bytes")
    print(f"  Final selected mode : {metrics['final_selected_mode']}")


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
        "compress-dir", help="Compress a directory to .mc1dir (corpus mode)"
    )
    p_compress_dir.add_argument("input_dir", help="Input directory path")
    p_compress_dir.add_argument("output", help="Output .mc1dir file path")
    p_compress_dir.add_argument(
        "--no-delta",
        action="store_true",
        default=False,
        help="Disable intra-chunk delta encoding (store every unique chunk verbatim).",
    )
    p_compress_dir.set_defaults(func=cmd_compress_dir)

    p_decompress_dir = sub.add_parser(
        "decompress-dir", help="Decompress a .mc1dir archive to a directory"
    )
    p_decompress_dir.add_argument("input", help="Input .mc1dir file path")
    p_decompress_dir.add_argument("output_dir", help="Output directory path")
    p_decompress_dir.set_defaults(func=cmd_decompress_dir)

    p_compare_dir = sub.add_parser(
        "compare-dir",
        help="Compare MC corpus / corpus-template / TAR+ZSTD on a directory",
    )
    p_compare_dir.add_argument("input_dir", help="Input directory path")
    p_compare_dir.set_defaults(func=cmd_compare_dir)

    p_compress_tpl = sub.add_parser(
        "compress-template-dir",
        help="Compress a directory to .mck (corpus template mode)",
    )
    p_compress_tpl.add_argument("input_dir", help="Input directory path")
    p_compress_tpl.add_argument("output", help="Output .mck file path")
    p_compress_tpl.set_defaults(func=cmd_compress_template_dir)

    p_decompress_tpl = sub.add_parser(
        "decompress-template-dir",
        help="Decompress a .mck corpus-template archive to a directory",
    )
    p_decompress_tpl.add_argument("input", help="Input .mck file path")
    p_decompress_tpl.add_argument("output_dir", help="Output directory path")
    p_decompress_tpl.set_defaults(func=cmd_decompress_template_dir)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
