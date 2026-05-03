"""Production-style validation benchmark for MetaCompressor corpus-template mode.

Creates a reproducible set of synthetic and semi-realistic corpora, benchmarks
multiple baselines, validates byte-for-byte correctness for all MC modes, and
writes machine-readable + Markdown reports under ``results/``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import sys
import tarfile
import tempfile
import time
import tracemalloc
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import zstandard as zstd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from metacompressor.corpus_template import (  # noqa: E402
    _MIN_TEMPLATE_OCCURRENCES,
    _MODE_COLUMNAR_V1,
    _MODE_RAW_TAR_ZSTD,
    _MODE_ROW_V1,
    _build_columnar_template_archive,
    _build_row_template_archive,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
    _template_string,
    _tokenize,
)


_RESULTS_DIR = REPO_ROOT / "results"
_MARKDOWN_PATH = _RESULTS_DIR / "metacompressor_production_validation.md"
_JSON_PATH = _RESULTS_DIR / "metacompressor_production_validation.json"

_ZSTD_LEVEL = 3
_GZIP_LEVEL = 6
_BROTLI_LEVEL = 4

_KB = 1024
_MB = 1024 * 1024

_STACK_TRACE_LINES = [
    'Traceback (most recent call last):\n',
    '  File "/srv/app/handlers.py", line 182, in handle\n',
    '  File "/srv/app/db.py", line 87, in query_user\n',
    'RuntimeError: upstream timeout after 2500ms\n',
]

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "curl/8.7.1",
    "okhttp/4.12.0",
    "Go-http-client/2.0",
    "python-requests/2.32.3",
]

_PATHS = [
    "/api/v1/login",
    "/api/v1/orders",
    "/api/v1/orders/checkout",
    "/api/v1/users/profile",
    "/internal/health",
    "/metrics",
    "/payments/capture",
    "/search?q=widgets",
]

_MESSAGE_VARIANTS = [
    "request completed",
    "request queued for retry",
    "cache miss resolved from db",
    "db query finished",
    "auth token refreshed",
    "feature flag snapshot loaded",
    "worker lease renewed",
]

_FREEFORM_SENTENCES = [
    "operator noted unusual packet drift near the edge gateway",
    "debug shell printed a partial response with no schema at all",
    "someone copied a stack trace into the log and left out the request id",
    "message body mixed prose with shell output and a pasted json fragment",
    "rotated logs contain comments, timestamps, and plain english notes",
    "an alert was acknowledged but the surrounding lines were mostly narrative",
]


class ValidationError(RuntimeError):
    """Raised when correctness or determinism fails."""


@dataclass
class DatasetSpec:
    name: str
    dataset_type: str
    realism: str
    structured: bool
    generator: Callable[[Path], None]


def _available_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 2048


def _fmt_bytes(size: Optional[int]) -> str:
    if size is None:
        return "n/a"
    if size < _KB:
        return "%d B" % size
    if size < _MB:
        return "%.1f KB" % (size / _KB)
    if size < 1024 * _MB:
        return "%.1f MB" % (size / _MB)
    return "%.2f GB" % (size / (1024 * _MB))


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return "%.1f%%" % value


def _mode_label(mode: str) -> str:
    if mode == _MODE_ROW_V1:
        return "row"
    if mode == _MODE_COLUMNAR_V1:
        return "columnar"
    if mode == _MODE_RAW_TAR_ZSTD:
        return "raw_tar_zstd"
    return mode


def _mode_verdict(delta_pct: float) -> str:
    if delta_pct <= -10.0:
        return "strong win"
    if delta_pct < 0.0:
        return "win"
    if delta_pct <= 10.0:
        return "acceptable"
    return "loss"


def _delta_pct(candidate_size: int, baseline_size: int) -> Optional[float]:
    if baseline_size <= 0:
        return None
    return ((candidate_size - baseline_size) / baseline_size) * 100.0


def _raw_reduction_pct(raw_size: int, compressed_size: int) -> Optional[float]:
    if raw_size <= 0:
        return None
    return (1.0 - (compressed_size / raw_size)) * 100.0


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _json_compact(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _iter_files(root: Path) -> List[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _random_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128)))


def _random_hex(rng: random.Random, nbytes: int) -> str:
    return rng.randbytes(nbytes).hex()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _append_lines_until_size(
    path: Path,
    target_size: int,
    line_factory: Callable[[int], List[str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    index = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        while written < target_size:
            lines = line_factory(index)
            chunk = "".join(lines)
            fh.write(chunk)
            written += len(chunk.encode("utf-8"))
            index += 1


def _build_deterministic_tar(input_dir: Path, tar_path: Path) -> None:
    with tarfile.open(tar_path, mode="w") as tar:
        for file_path in _iter_files(input_dir):
            data = file_path.read_bytes()
            info = tarfile.TarInfo(name=file_path.relative_to(input_dir).as_posix())
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))


def _compare_trees(original_dir: Path, restored_dir: Path) -> None:
    original_files = [path.relative_to(original_dir).as_posix() for path in _iter_files(original_dir)]
    restored_files = [path.relative_to(restored_dir).as_posix() for path in _iter_files(restored_dir)]
    if original_files != restored_files:
        raise ValidationError(
            "file set mismatch: original=%s restored=%s" % (original_files, restored_files)
        )
    for rel in original_files:
        original_bytes = (original_dir / rel).read_bytes()
        restored_bytes = (restored_dir / rel).read_bytes()
        if original_bytes != restored_bytes:
            raise ValidationError("byte mismatch for %s" % rel)


def _measure_peak_mb(func: Callable[[], Tuple[Any, Any]]) -> Tuple[Any, Any, float]:
    tracemalloc.start()
    try:
        result_a, result_b = func()
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result_a, result_b, peak_bytes / _MB


def _brotli_available() -> bool:
    try:
        import brotli  # type: ignore[import]

        return True
    except Exception:
        return False


def _gzip_from_tar(tar_path: Path, output_path: Path) -> None:
    with tar_path.open("rb") as src, output_path.open("wb") as raw_out:
        with gzip.GzipFile(
            filename="",
            fileobj=raw_out,
            mode="wb",
            compresslevel=_GZIP_LEVEL,
            mtime=0,
        ) as gz:
            shutil.copyfileobj(src, gz, length=1024 * 1024)


def _brotli_from_tar(tar_path: Path, output_path: Path) -> None:
    import brotli  # type: ignore[import]

    compressor = brotli.Compressor(quality=_BROTLI_LEVEL)
    with tar_path.open("rb") as src, output_path.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out = compressor.process(chunk)
            if out:
                dst.write(out)
        dst.write(compressor.finish())


def _prepare_template_context(input_dir: Path) -> Dict[str, Any]:
    t_extract_start = time.perf_counter()
    all_files = _iter_files(input_dir)
    file_meta = []
    tok_cache = {}
    tpl_count = {}
    total_lines = 0

    t_tokenize_start = time.perf_counter()
    for file_path in all_files:
        rel = file_path.relative_to(input_dir).as_posix()
        raw = file_path.read_bytes()
        try:
            text = raw.decode("utf-8")
            lines = text.split("\n")
            file_meta.append((rel, False))
            for line in lines:
                total_lines += 1
                if line not in tok_cache:
                    tok_cache[line] = _tokenize(line)
                template_key = tok_cache[line][0]
                tpl_count[template_key] = tpl_count.get(template_key, 0) + 1
        except UnicodeDecodeError:
            file_meta.append((rel, True))
    tokenize_s = time.perf_counter() - t_tokenize_start

    tpl_to_id = {}
    tpl_strings = []
    for template_key, count in tpl_count.items():
        if count >= _MIN_TEMPLATE_OCCURRENCES:
            tpl_to_id[template_key] = len(tpl_strings)
            tpl_strings.append(_template_string(template_key))

    extract_s = time.perf_counter() - t_extract_start
    return {
        "all_files": all_files,
        "file_meta": file_meta,
        "tok_cache": tok_cache,
        "tpl_to_id": tpl_to_id,
        "tpl_strings": tpl_strings,
        "total_lines": total_lines,
        "tokenize_s": tokenize_s,
        "count_s": 0.0,
        "extract_s": extract_s,
    }


def _compress_forced_mode(input_dir: Path, mode: str) -> Tuple[bytes, Dict[str, Any]]:
    total_start = time.perf_counter()
    context = _prepare_template_context(input_dir)
    all_files = context["all_files"]
    file_meta = context["file_meta"]
    tok_cache = context["tok_cache"]
    tpl_to_id = context["tpl_to_id"]
    tpl_strings = context["tpl_strings"]
    total_lines = context["total_lines"]

    if mode == _MODE_ROW_V1:
        archive, stats = _build_row_template_archive(
            input_dir=input_dir,
            all_files=all_files,
            file_meta=file_meta,
            tok_cache=tok_cache,
            tpl_to_id=tpl_to_id,
            tpl_strings=tpl_strings,
        )
        column_stats = {
            "num_columnar_templates": 0,
            "num_encoded_columns": 0,
            "column_encoding_counts": {},
            "raw_column_fallback_count": 0,
        }
    elif mode == _MODE_COLUMNAR_V1:
        archive, stats = _build_columnar_template_archive(
            all_files=all_files,
            file_meta=file_meta,
            tok_cache=tok_cache,
            tpl_to_id=tpl_to_id,
            tpl_strings=tpl_strings,
        )
        column_stats = {
            "num_columnar_templates": stats["num_columnar_templates"],
            "num_encoded_columns": stats["num_encoded_columns"],
            "column_encoding_counts": stats["column_encoding_counts"],
            "raw_column_fallback_count": stats["raw_column_fallback_count"],
        }
    else:
        raise ValueError("unsupported mode: %s" % mode)

    template_reuse_count = stats["template_reuse_count"]
    reuse_rate = (
        template_reuse_count / total_lines if total_lines > 0 else 0.0
    )
    avg_vars = (
        stats["total_var_slots"] / template_reuse_count
        if template_reuse_count > 0
        else 0.0
    )
    total_s = time.perf_counter() - total_start
    metrics = {
        "num_files": len(all_files),
        "num_lines": total_lines,
        "num_shared_templates": len(tpl_strings),
        "template_reuse_count": template_reuse_count,
        "template_reuse_rate": reuse_rate,
        "raw_fallback_lines": stats["raw_fallback_lines"],
        "binary_fallback_files": stats["binary_fallback_files"],
        "low_structure_fallback_files": stats["low_structure_fallback_files"],
        "avg_vars_per_tpl_line": avg_vars,
        "compressed_size": len(archive),
        "tarzstd_size": None,
        "chose_raw_fallback": False,
        "columnar_enabled": mode == _MODE_COLUMNAR_V1,
        "num_columnar_templates": column_stats["num_columnar_templates"],
        "num_encoded_columns": column_stats["num_encoded_columns"],
        "column_encoding_counts": column_stats["column_encoding_counts"],
        "raw_column_fallback_count": column_stats["raw_column_fallback_count"],
        "columnar_size": len(archive) if mode == _MODE_COLUMNAR_V1 else None,
        "row_mode_size": len(archive) if mode == _MODE_ROW_V1 else None,
        "columnar_savings_vs_row": None,
        "final_selected_mode": mode,
        "timing": {
            "tokenize_s": context["tokenize_s"],
            "count_s": context["count_s"],
            "encode_s": stats["encode_s"],
            "extract_s": context["extract_s"],
            "serialize_s": stats["serialize_s"],
            "zstd_s": 0.0,
            "total_s": total_s,
        },
    }
    return archive, metrics


def _run_mc_mode(input_dir: Path, mode: str, work_dir: Path) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if mode == "auto":
        compress_func = lambda: compress_corpus_template_with_metrics(input_dir)
    elif mode == _MODE_ROW_V1:
        compress_func = lambda: _compress_forced_mode(input_dir, _MODE_ROW_V1)
    elif mode == _MODE_COLUMNAR_V1:
        compress_func = lambda: _compress_forced_mode(input_dir, _MODE_COLUMNAR_V1)
    else:
        raise ValueError("unsupported MC mode: %s" % mode)

    started = time.perf_counter()

    def _compress_once() -> Tuple[bytes, Dict[str, Any]]:
        return compress_func()

    archive, metrics, peak_mb = _measure_peak_mb(_compress_once)
    compress_s = time.perf_counter() - started

    archive_2, _ = compress_func()
    if archive != archive_2:
        raise ValidationError("determinism failure for %s" % mode)

    out_dir = work_dir / ("restore_%s" % _mode_label(metrics["final_selected_mode"]))
    if out_dir.exists():
        shutil.rmtree(out_dir)
    t0 = time.perf_counter()
    decompress_corpus_template(archive, out_dir)
    decompress_s = time.perf_counter() - t0
    _compare_trees(input_dir, out_dir)

    return {
        "size": len(archive),
        "compress_s": compress_s,
        "decompress_s": decompress_s,
        "peak_mem_mb": peak_mb,
        "metrics": metrics,
    }


def _run_zstd_per_file(input_dir: Path, work_dir: Path) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = work_dir / "zstd_per_file"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    dctx = zstd.ZstdDecompressor()

    def _compress() -> Tuple[int, None]:
        total_size = 0
        for file_path in _iter_files(input_dir):
            rel = file_path.relative_to(input_dir)
            compressed = cctx.compress(file_path.read_bytes())
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(compressed)
            total_size += len(compressed)
        return total_size, None

    t0 = time.perf_counter()
    size, _, peak_mb = _measure_peak_mb(_compress)
    compress_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    for file_path in _iter_files(out_dir):
        dctx.decompress(file_path.read_bytes())
    decompress_s = time.perf_counter() - t1

    return {
        "size": size,
        "compress_s": compress_s,
        "decompress_s": decompress_s,
        "peak_mem_mb": peak_mb,
    }


def _run_tar_baseline(
    input_dir: Path,
    work_dir: Path,
    label: str,
    compressor: Callable[[Path, Path], None],
    decompressor: Callable[[Path, Path], None],
) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    tar_path = work_dir / ("%s.tar" % label)
    archive_path = work_dir / ("%s.archive" % label)
    restored_tar = work_dir / ("%s.restored.tar" % label)
    restored_dir = work_dir / ("%s.restored" % label)

    def _compress() -> Tuple[int, None]:
        _build_deterministic_tar(input_dir, tar_path)
        compressor(tar_path, archive_path)
        return archive_path.stat().st_size, None

    t0 = time.perf_counter()
    size, _, peak_mb = _measure_peak_mb(_compress)
    compress_s = time.perf_counter() - t0

    if restored_dir.exists():
        shutil.rmtree(restored_dir)
    restored_dir.mkdir(parents=True, exist_ok=True)
    t1 = time.perf_counter()
    decompressor(archive_path, restored_tar)
    with tarfile.open(restored_tar, mode="r") as tar:
        tar.extractall(restored_dir, filter="data")
    decompress_s = time.perf_counter() - t1

    return {
        "size": size,
        "compress_s": compress_s,
        "decompress_s": decompress_s,
        "peak_mem_mb": peak_mb,
    }


def _zstd_tar_compressor(tar_path: Path, archive_path: Path) -> None:
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
    with tar_path.open("rb") as src, archive_path.open("wb") as dst:
        cctx.copy_stream(src, dst)


def _zstd_tar_decompressor(archive_path: Path, tar_path: Path) -> None:
    dctx = zstd.ZstdDecompressor()
    with archive_path.open("rb") as src, tar_path.open("wb") as dst:
        dctx.copy_stream(src, dst)


def _gzip_tar_compressor(tar_path: Path, archive_path: Path) -> None:
    _gzip_from_tar(tar_path, archive_path)


def _gzip_tar_decompressor(archive_path: Path, tar_path: Path) -> None:
    with archive_path.open("rb") as src, gzip.GzipFile(fileobj=src, mode="rb") as gz, tar_path.open("wb") as dst:
        shutil.copyfileobj(gz, dst, length=1024 * 1024)


def _brotli_tar_compressor(tar_path: Path, archive_path: Path) -> None:
    _brotli_from_tar(tar_path, archive_path)


def _brotli_tar_decompressor(archive_path: Path, tar_path: Path) -> None:
    import brotli  # type: ignore[import]

    decompressor = brotli.Decompressor()
    with archive_path.open("rb") as src, tar_path.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(decompressor.process(chunk))


def _service_line_factory(
    rng: random.Random,
    service: str,
    file_id: int,
) -> Callable[[int], List[str]]:
    def factory(index: int) -> List[str]:
        ts = "2026-02-%02dT%02d:%02d:%02d.%03dZ" % (
            (index % 27) + 1,
            (index // 3600) % 24,
            (index // 60) % 60,
            index % 60,
            index % 1000,
        )
        request_id = _random_uuid(rng)
        trace_id = _random_hex(rng, 8)
        user_id = str(1000 + (index % 4096))
        ip = "10.%d.%d.%d" % ((file_id % 5) + 1, index % 255, (index * 7) % 255)
        base = [
            '%s level=%s service=%s trace_id=%s request_id=%s method=%s path=%s status=%d latency_ms=%d msg="%s" ip=%s'
            % (
                ts,
                ["INFO", "WARN", "ERROR", "DEBUG"][index % 4],
                service,
                trace_id,
                request_id,
                ["GET", "POST", "PUT", "DELETE"][index % 4],
                _PATHS[index % len(_PATHS)],
                [200, 201, 204, 400, 404, 429, 500][index % 7],
                4 + (index % 2500),
                _MESSAGE_VARIANTS[index % len(_MESSAGE_VARIANTS)],
                ip,
            )
        ]
        if index % 5:
            base[0] += " user_id=%s" % user_id
        if index % 7:
            base[0] += " region=%s" % ["us-east-1", "eu-west-1", "ap-southeast-2"][index % 3]
        base[0] += "\n"
        if index % 111 == 0:
            error_prefix = (
                '%s level=ERROR service=%s trace_id=%s request_id=%s msg="database timeout" db_host=db-%d shard=%d\n'
                % (ts, service, trace_id, request_id, index % 5, index % 16)
            )
            return [error_prefix] + _STACK_TRACE_LINES
        return base

    return factory


def _generate_app_service_logs(root: Path, target_mb: int, seed: int, files: int) -> None:
    rng = random.Random(seed)
    per_file = (target_mb * _MB) // files
    services = [
        "auth-service",
        "billing-service",
        "catalog-service",
        "gateway",
        "notifications",
        "worker",
    ]
    for file_id in range(files):
        service = services[file_id % len(services)]
        _append_lines_until_size(
            root / ("services/%s-%02d.log" % (service, file_id)),
            per_file,
            _service_line_factory(rng, service, file_id),
        )


def _generate_ndjson_logs(root: Path, target_mb: int, seed: int, files: int) -> None:
    rng = random.Random(seed)
    per_file = (target_mb * _MB) // files

    def factory_builder(stream_id: int) -> Callable[[int], List[str]]:
        def factory(index: int) -> List[str]:
            record = {
                "ts": "2026-03-%02dT%02d:%02d:%02dZ"
                % ((index % 27) + 1, (index // 3600) % 24, (index // 60) % 60, index % 60),
                "service": ["api", "auth", "catalog", "worker", "queue"][stream_id % 5],
                "level": ["INFO", "WARN", "ERROR"][index % 3],
                "request_id": _random_uuid(rng),
                "trace_id": _random_hex(rng, 8),
                "route": _PATHS[index % len(_PATHS)],
                "status": [200, 201, 202, 400, 404, 429, 500][index % 7],
                "latency_ms": 5 + (index % 5000),
                "user_agent": _USER_AGENTS[index % len(_USER_AGENTS)],
                "variant": _MESSAGE_VARIANTS[index % len(_MESSAGE_VARIANTS)],
            }
            if index % 4 != 0:
                record["user_id"] = "user-%d" % (index % 10000)
            if index % 6 == 0:
                record["tags"] = ["prod", "canary" if index % 12 == 0 else "stable"]
            if index % 97 == 0:
                record["exception"] = {
                    "type": "RuntimeError",
                    "message": "connection reset by peer",
                    "frames": ["svc.handler", "svc.db", "svc.retry"],
                }
            return [_json_compact(record) + "\n"]

        return factory

    for stream_id in range(files):
        _append_lines_until_size(
            root / ("ndjson/events-%02d.ndjson" % stream_id),
            per_file,
            factory_builder(stream_id),
        )


def _generate_nginx_logs(root: Path, target_mb: int, seed: int, files: int) -> None:
    rng = random.Random(seed)
    per_file = (target_mb * _MB) // files

    def factory_builder(file_id: int) -> Callable[[int], List[str]]:
        def factory(index: int) -> List[str]:
            user_agent = _USER_AGENTS[index % len(_USER_AGENTS)]
            path = _PATHS[index % len(_PATHS)]
            line = (
                '192.168.%d.%d - - [%02d/Mar/2026:%02d:%02d:%02d +0000] "%s %s HTTP/1.1" %d %d "-" "%s" rt=%.4f upstream=%s trace=%s\n'
                % (
                    file_id % 255,
                    (index * 11) % 255,
                    (index % 28) + 1,
                    (index // 3600) % 24,
                    (index // 60) % 60,
                    index % 60,
                    ["GET", "POST", "PUT", "DELETE"][index % 4],
                    path,
                    [200, 201, 204, 301, 302, 404, 429, 500][index % 8],
                    300 + ((index * 29) % 120000),
                    user_agent,
                    0.001 + ((index % 2000) / 1000.0),
                    ["catalog", "auth", "payments", "search"][index % 4],
                    _random_hex(rng, 6),
                )
            )
            return [line]

        return factory

    for file_id in range(files):
        _append_lines_until_size(
            root / ("nginx/access-%02d.log" % file_id),
            per_file,
            factory_builder(file_id),
        )


def _generate_mixed_microservice_logs(root: Path, target_mb: int, seed: int) -> None:
    rng = random.Random(seed)
    _generate_app_service_logs(root / "plaintext", max(1, target_mb // 2), seed + 1, 8)
    _generate_ndjson_logs(root / "json", max(1, target_mb // 3), seed + 2, 4)
    _generate_nginx_logs(root / "edge", max(1, target_mb // 6), seed + 3, 2)

    extra = root / "worker/worker.out"
    per_file = max(1, (target_mb * _MB) // 8)

    def worker_factory(index: int) -> List[str]:
        line = (
            "queue=%s shard=%d lease_id=%s job=%s attempt=%d result=%s duration_ms=%d\n"
            % (
                ["email", "billing", "reindex", "cleanup"][index % 4],
                index % 16,
                _random_uuid(rng),
                ["send-email", "capture-payment", "refresh-cache", "sync-search"][index % 4],
                index % 5,
                ["success", "retry", "timeout"][index % 3],
                50 + (index % 4000),
            )
        )
        return [line]

    _append_lines_until_size(extra, per_file, worker_factory)


def _generate_high_cardinality_logs(root: Path, target_mb: int, seed: int, files: int) -> None:
    rng = random.Random(seed)
    per_file = (target_mb * _MB) // files

    def factory_builder(file_id: int) -> Callable[[int], List[str]]:
        def factory(index: int) -> List[str]:
            payload = hashlib.sha1(
                ("%d-%d-%s" % (file_id, index, _random_hex(rng, 12))).encode("utf-8")
            ).hexdigest()
            line = (
                "ts=2026-04-%02dT%02d:%02d:%02dZ env=prod tenant=%s request_id=%s session=%s ua_hash=%s path=/blob/%s status=%d payload_sha1=%s\n"
                % (
                    (index % 28) + 1,
                    (index // 3600) % 24,
                    (index // 60) % 60,
                    index % 60,
                    _random_hex(rng, 4),
                    _random_uuid(rng),
                    _random_hex(rng, 12),
                    _random_hex(rng, 8),
                    payload[:16],
                    [200, 202, 400, 404, 429, 500][index % 6],
                    payload,
                )
            )
            return [line]

        return factory

    for file_id in range(files):
        _append_lines_until_size(
            root / ("highcard/request-%02d.log" % file_id),
            per_file,
            factory_builder(file_id),
        )


def _generate_noisy_logs(root: Path, target_mb: int, seed: int, files: int) -> None:
    rng = random.Random(seed)
    per_file = (target_mb * _MB) // files

    def factory_builder(file_id: int) -> Callable[[int], List[str]]:
        def factory(index: int) -> List[str]:
            kind = index % 6
            if kind == 0:
                return [
                    "note=%s context=%s\n"
                    % (
                        _FREEFORM_SENTENCES[index % len(_FREEFORM_SENTENCES)],
                        _random_hex(rng, 5),
                    )
                ]
            if kind == 1:
                return [
                    "$ kubectl get pods --namespace app-%d\npod/%s Running\n"
                    % (file_id % 5, _random_hex(rng, 4))
                ]
            if kind == 2:
                return _STACK_TRACE_LINES[:]
            if kind == 3:
                return [
                    '{"fragment": true, "line": %d, "maybe": "%s"}\n'
                    % (index, _MESSAGE_VARIANTS[index % len(_MESSAGE_VARIANTS)])
                ]
            if kind == 4:
                return [
                    "WARN something odd happened without a stable schema idx=%d detail=%s\n"
                    % (index, _random_hex(rng, 10))
                ]
            return [
                "2026-05-%02d mixed text and kv pairs retry=%d upstream=%s\n"
                % ((index % 28) + 1, index % 4, _random_hex(rng, 6))
            ]

        return factory

    for file_id in range(files):
        _append_lines_until_size(
            root / ("noisy/noisy-%02d.log" % file_id),
            per_file,
            factory_builder(file_id),
        )


def _generate_binary_compressed_mix(root: Path, seed: int) -> None:
    rng = random.Random(seed)
    _generate_app_service_logs(root / "text", 8, seed + 1, 4)
    _generate_ndjson_logs(root / "json", 4, seed + 2, 2)

    raw_payload = ("INFO event=1 status=200 path=/api variant=warmup\n" * 20000).encode("utf-8")
    gz_path = root / "precompressed/archive.tar.gz"
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    with gz_path.open("wb") as raw_out:
        with gzip.GzipFile(filename="", fileobj=raw_out, mode="wb", mtime=0) as gz:
            gz.write(raw_payload)

    zst_path = root / "precompressed/archive.tar.zst"
    zst_path.write_bytes(zstd.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw_payload))

    _write_bytes(root / "binary/blob-01.bin", rng.randbytes(4 * _MB))
    _write_bytes(root / "binary/blob-02.bin", rng.randbytes(6 * _MB))
    _write_bytes(root / "binary/blob-03.bin", rng.randbytes(3 * _MB))


def _generate_many_small_files(root: Path, seed: int, files: int) -> None:
    rng = random.Random(seed)
    for index in range(files):
        ext = [".log", ".json", ".txt", ".cfg"][index % 4]
        rel = "small/%02d/shard-%05d%s" % (index % 100, index, ext)
        path = root / rel
        if ext == ".json":
            payload = {
                "service": ["api", "auth", "worker", "web"][index % 4],
                "request_id": _random_uuid(rng),
                "status": [200, 201, 404, 500][index % 4],
                "latency_ms": 10 + (index % 900),
            }
            _write_bytes(path, (_json_dumps(payload) + "\n").encode("utf-8"))
        elif ext == ".cfg":
            _write_bytes(
                path,
                (
                    "port=%d\nretries=%d\nenabled=%s\nroute=%s\n"
                    % (
                        8000 + (index % 128),
                        index % 5,
                        "true" if index % 2 else "false",
                        _PATHS[index % len(_PATHS)],
                    )
                ).encode("utf-8"),
            )
        elif ext == ".txt":
            _write_bytes(
                path,
                (
                    "operator note %s\n%s\n"
                    % (
                        _MESSAGE_VARIANTS[index % len(_MESSAGE_VARIANTS)],
                        _FREEFORM_SENTENCES[index % len(_FREEFORM_SENTENCES)],
                    )
                ).encode("utf-8"),
            )
        else:
            _write_bytes(
                path,
                (
                    "ts=2026-06-%02d service=%s request_id=%s status=%d latency_ms=%d path=%s\n"
                    % (
                        (index % 28) + 1,
                        ["api", "auth", "worker", "gateway"][index % 4],
                        _random_uuid(rng),
                        [200, 201, 404, 500][index % 4],
                        5 + (index % 2000),
                        _PATHS[index % len(_PATHS)],
                    )
                ).encode("utf-8"),
            )


def _dataset_specs(include_very_large: bool) -> List[DatasetSpec]:
    specs = [
        DatasetSpec(
            name="app_service_logs",
            dataset_type="app/service logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 12, seed=101, files=10),
        ),
        DatasetSpec(
            name="json_ndjson_logs",
            dataset_type="JSON/NDJSON logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_ndjson_logs(root, 14, seed=202, files=8),
        ),
        DatasetSpec(
            name="nginx_access_logs",
            dataset_type="nginx/access logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_nginx_logs(root, 14, seed=303, files=6),
        ),
        DatasetSpec(
            name="mixed_microservice_logs",
            dataset_type="mixed microservice logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_mixed_microservice_logs(root, 18, seed=404),
        ),
        DatasetSpec(
            name="high_cardinality_logs",
            dataset_type="high-cardinality logs",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_high_cardinality_logs(root, 10, seed=505, files=6),
        ),
        DatasetSpec(
            name="noisy_low_structure_logs",
            dataset_type="noisy/low-structure logs",
            realism="semi-realistic",
            structured=False,
            generator=lambda root: _generate_noisy_logs(root, 9, seed=606, files=6),
        ),
        DatasetSpec(
            name="binary_compressed_mix",
            dataset_type="binary/compressed mixed corpus",
            realism="semi-realistic",
            structured=False,
            generator=lambda root: _generate_binary_compressed_mix(root, seed=707),
        ),
        DatasetSpec(
            name="large_corpus_128mb",
            dataset_type="large corpus: 100MB+",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_app_service_logs(root, 128, seed=808, files=20),
        ),
        DatasetSpec(
            name="many_small_files_5000",
            dataset_type="many-small-files corpus",
            realism="semi-realistic",
            structured=True,
            generator=lambda root: _generate_many_small_files(root, seed=909, files=5000),
        ),
    ]
    if include_very_large:
        specs.append(
            DatasetSpec(
                name="very_large_corpus_512mb",
                dataset_type="very large corpus: 500MB+",
                realism="synthetic",
                structured=True,
                generator=lambda root: _generate_app_service_logs(root, 512, seed=1001, files=32),
            )
        )
    return specs


def _build_dataset(dataset_dir: Path, spec: DatasetSpec) -> None:
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    spec.generator(dataset_dir)


def _measure_dataset(dataset_dir: Path, spec: DatasetSpec, work_dir: Path) -> Dict[str, Any]:
    raw_size = sum(path.stat().st_size for path in _iter_files(dataset_dir))
    methods: Dict[str, Optional[Dict[str, Any]]] = {}

    methods["zstd_per_file"] = _run_zstd_per_file(dataset_dir, work_dir / "zstd_per_file")
    methods["tar_zstd"] = _run_tar_baseline(
        dataset_dir,
        work_dir / "tar_zstd",
        "tar_zstd",
        _zstd_tar_compressor,
        _zstd_tar_decompressor,
    )
    methods["gzip"] = _run_tar_baseline(
        dataset_dir,
        work_dir / "gzip",
        "gzip",
        _gzip_tar_compressor,
        _gzip_tar_decompressor,
    )

    if _brotli_available():
        methods["brotli"] = _run_tar_baseline(
            dataset_dir,
            work_dir / "brotli",
            "brotli",
            _brotli_tar_compressor,
            _brotli_tar_decompressor,
        )
    else:
        methods["brotli"] = None

    methods["mc_row_template"] = _run_mc_mode(dataset_dir, _MODE_ROW_V1, work_dir / "mc_row")
    methods["mc_columnar_template"] = _run_mc_mode(
        dataset_dir,
        _MODE_COLUMNAR_V1,
        work_dir / "mc_columnar",
    )
    methods["mc_final_selected"] = _run_mc_mode(dataset_dir, "auto", work_dir / "mc_final")

    tar_size = methods["tar_zstd"]["size"]  # type: ignore[index]
    zstd_size = methods["zstd_per_file"]["size"]  # type: ignore[index]
    final_metrics = methods["mc_final_selected"]["metrics"]  # type: ignore[index]
    final_size = methods["mc_final_selected"]["size"]  # type: ignore[index]

    column_encoding_counts = final_metrics["column_encoding_counts"]
    total_column_count = sum(column_encoding_counts.values())
    delta_tar_pct = _delta_pct(final_size, tar_size)
    delta_zstd_pct = _delta_pct(final_size, zstd_size)
    raw_reduction_pct = _raw_reduction_pct(raw_size, final_size)

    return {
        "name": spec.name,
        "dataset_type": spec.dataset_type,
        "realism": spec.realism,
        "structured": spec.structured,
        "raw_size": raw_size,
        "methods": methods,
        "mc_summary": {
            "selected_mode": final_metrics["final_selected_mode"],
            "fallback_triggered": bool(final_metrics["chose_raw_fallback"]),
            "template_count": final_metrics["num_shared_templates"],
            "template_reuse_rate": final_metrics["template_reuse_rate"],
            "column_count": total_column_count,
            "column_encoding_counts": column_encoding_counts,
            "raw_fallback_lines": final_metrics["raw_fallback_lines"],
            "raw_fallback_files": final_metrics["low_structure_fallback_files"],
            "binary_fallback_files": final_metrics["binary_fallback_files"],
            "delta_vs_tar_zstd_pct": delta_tar_pct,
            "delta_vs_zstd_per_file_pct": delta_zstd_pct,
            "reduction_vs_raw_pct": raw_reduction_pct,
            "verdict": _mode_verdict(delta_tar_pct if delta_tar_pct is not None else 0.0),
        },
    }


def _build_final_verdict(dataset_results: List[Dict[str, Any]]) -> str:
    realistic_wins = 0
    structured_regression = False
    for result in dataset_results:
        delta_pct = result["mc_summary"]["delta_vs_tar_zstd_pct"]
        if delta_pct is not None and result["realism"] in ("semi-realistic", "real-world"):
            if delta_pct <= -10.0:
                realistic_wins += 1
        if result["structured"] and delta_pct is not None and delta_pct > 10.0:
            structured_regression = True

    if realistic_wins >= 3 and not structured_regression:
        return "PRODUCTION_EDGE_CONFIRMED"
    return "PRODUCTION_EDGE_PARTIAL"


def _build_markdown_report(
    dataset_results: List[Dict[str, Any]],
    final_verdict: str,
    brotli_available: bool,
) -> str:
    lines = [
        "# MetaCompressor Production Validation Report",
        "",
        "Generated by `benchmarks/production_validation.py`.",
        "",
        "**Compression levels:** ZSTD level 3, TAR+ZSTD level 3, gzip level 6, "
        "brotli level 4 when installed.",
        "",
        "**Memory note:** Peak MB is Python `tracemalloc` peak for each measured method.",
        "",
        "**Final verdict:** `%s`" % final_verdict,
        "",
        "**Correctness / determinism:** all MC row, columnar, and final archives "
        "were decompressed and byte-compared against the source corpus; repeated "
        "compressions matched byte-for-byte.",
        "",
        "| Dataset | Type | Raw | TAR+ZSTD | ZSTD/file | MC final | MC mode | Delta vs TAR+ZSTD | Compress s | Decompress s | Peak MB | Verdict |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|",
    ]

    for result in dataset_results:
        final_method = result["methods"]["mc_final_selected"]
        tar_method = result["methods"]["tar_zstd"]
        zstd_method = result["methods"]["zstd_per_file"]
        summary = result["mc_summary"]
        lines.append(
            "| %s | %s / %s | %s | %s | %s | %s | %s | %s | %.3f | %.3f | %.1f | %s |"
            % (
                result["name"],
                result["dataset_type"],
                result["realism"],
                _fmt_bytes(result["raw_size"]),
                _fmt_bytes(tar_method["size"]),
                _fmt_bytes(zstd_method["size"]),
                _fmt_bytes(final_method["size"]),
                _mode_label(summary["selected_mode"]),
                _fmt_pct(summary["delta_vs_tar_zstd_pct"]),
                final_method["compress_s"],
                final_method["decompress_s"],
                final_method["peak_mem_mb"],
                summary["verdict"],
            )
        )

    reductions_hold = []
    reductions_do_not_hold = []
    for result in dataset_results:
        reduction_pct = result["mc_summary"]["reduction_vs_raw_pct"]
        entry = "- **%s**: %s raw reduction; mode=%s" % (
            result["name"],
            _fmt_pct(reduction_pct),
            _mode_label(result["mc_summary"]["selected_mode"]),
        )
        if reduction_pct is not None and 90.0 <= reduction_pct <= 95.0:
            reductions_hold.append(entry)
        else:
            reductions_do_not_hold.append(entry)

    wins = [
        result
        for result in dataset_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] <= -1.0
    ]
    losses = [
        result
        for result in dataset_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] >= 1.0
    ]
    fallback_datasets = [
        result["name"]
        for result in dataset_results
        if result["mc_summary"]["fallback_triggered"]
    ]

    lines += [
        "",
        "## Where 90–95% reduction holds",
        "",
    ]
    if reductions_hold:
        lines.extend(reductions_hold)
    else:
        lines.append("*(none in this validation run)*")

    lines += [
        "",
        "## Where it does not hold",
        "",
    ]
    lines.extend(reductions_do_not_hold)

    lines += [
        "",
        "## Why MC wins",
        "",
    ]
    if wins:
        for result in wins:
            summary = result["mc_summary"]
            lines.append(
                "- **%s**: shared templates=%d, reuse=%s, columns=%d"
                % (
                    result["name"],
                    summary["template_count"],
                    _fmt_pct(summary["template_reuse_rate"] * 100.0),
                    summary["column_count"],
                )
            )
        lines += [
            "",
            "MC wins when repeated structure spans many files, template reuse stays high, "
            "and the variable columns compress better after row/column separation than a "
            "generic TAR+ZSTD stream.",
        ]
    else:
        lines.append("*(no dataset beat TAR+ZSTD in this run)*")

    lines += [
        "",
        "## Why MC loses",
        "",
    ]
    if losses:
        for result in losses:
            summary = result["mc_summary"]
            lines.append(
                "- **%s**: delta=%s, reuse=%s, binary fallback files=%d"
                % (
                    result["name"],
                    _fmt_pct(summary["delta_vs_tar_zstd_pct"]),
                    _fmt_pct(summary["template_reuse_rate"] * 100.0),
                    summary["binary_fallback_files"],
                )
            )
        lines += [
            "",
            "MC loses when literals are already high-cardinality, structure is weak, or "
            "the corpus is dominated by binary/pre-compressed payloads that template "
            "extraction cannot improve.",
        ]
    else:
        lines.append("*(no dataset lost to TAR+ZSTD in this run)*")

    lines += [
        "",
        "## Fallback behavior",
        "",
        "- Raw fallback triggered on final selection: %s"
        % (", ".join(fallback_datasets) if fallback_datasets else "none"),
    ]
    for result in dataset_results:
        summary = result["mc_summary"]
        lines.append(
            "- **%s**: raw fallback lines=%d, low-structure fallback files=%d, binary fallback files=%d"
            % (
                result["name"],
                summary["raw_fallback_lines"],
                summary["raw_fallback_files"],
                summary["binary_fallback_files"],
            )
        )

    lines += [
        "",
        "## Performance tradeoffs",
        "",
        "MC final compression is slower than generic baselines because it tokenises the corpus, "
        "counts shared templates, and then emits row/column encodings before zstd.",
        "",
        "Decompression stays comparatively modest because output reconstruction is linear once the "
        "archive format is chosen.",
        "",
        "## Memory risks",
        "",
        "Peak memory is highest on the largest structured corpora because the token cache and archive "
        "builder hold more Python objects during analysis. `tracemalloc` does not include all native "
        "allocator usage, so real RSS may be higher.",
        "",
        "## Recommendation",
        "",
    ]

    if final_verdict == "PRODUCTION_EDGE_CONFIRMED":
        lines.append(
            "Use corpus-template mode for structured multi-file log corpora, but treat the 90–95% "
            "claim as dataset-specific rather than universal."
        )
    else:
        lines.append(
            "Keep positioning the current columnar/template path as strong on selected structured "
            "corpora, but not yet a broad production-wide 90–95% reduction across realistic logs."
        )

    lines += [
        "",
        "## Dataset details",
        "",
    ]

    for result in dataset_results:
        summary = result["mc_summary"]
        lines += [
            "### %s" % result["name"],
            "",
            "- Type: %s" % result["dataset_type"],
            "- Realism: %s" % result["realism"],
            "- Raw size: %s" % _fmt_bytes(result["raw_size"]),
            "- Selected MC mode: `%s`" % summary["selected_mode"],
            "- Delta vs TAR+ZSTD: %s" % _fmt_pct(summary["delta_vs_tar_zstd_pct"]),
            "- Delta vs ZSTD per-file: %s" % _fmt_pct(summary["delta_vs_zstd_per_file_pct"]),
            "- Raw reduction: %s" % _fmt_pct(summary["reduction_vs_raw_pct"]),
            "- Template count: %d" % summary["template_count"],
            "- Template reuse rate: %s" % _fmt_pct(summary["template_reuse_rate"] * 100.0),
            "- Column count: %d" % summary["column_count"],
            "- Column encodings: `%s`" % json.dumps(summary["column_encoding_counts"], sort_keys=True),
            "- Low-structure fallback files: %d" % summary["raw_fallback_files"],
            "- Binary fallback files: %d" % summary["binary_fallback_files"],
            "",
            "| Method | Size | Ratio | Δ vs TAR+ZSTD | Compress s | Decompress s | Peak MB |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for method_name in [
            "tar_zstd",
            "zstd_per_file",
            "gzip",
            "brotli",
            "mc_row_template",
            "mc_columnar_template",
            "mc_final_selected",
        ]:
            method = result["methods"][method_name]
            if method is None:
                lines.append("| %s | n/a | n/a | n/a | n/a | n/a | n/a |" % method_name)
                continue
            ratio = (
                result["raw_size"] / method["size"]
                if method["size"] > 0
                else 0.0
            )
            lines.append(
                "| %s | %s | %.2fx | %s | %.3f | %.3f | %.1f |"
                % (
                    method_name,
                    _fmt_bytes(method["size"]),
                    ratio,
                    _fmt_pct(_delta_pct(method["size"], result["methods"]["tar_zstd"]["size"])),
                    method["compress_s"],
                    method["decompress_s"],
                    method["peak_mem_mb"],
                )
            )
        lines.append("")

    if not brotli_available:
        lines += [
            "## Brotli availability",
            "",
            "brotli was not installed in this environment, so the optional brotli baseline is reported as unavailable.",
            "",
        ]

    return "\n".join(lines) + "\n"


def run_validation(output_dir: Optional[Path] = None, include_very_large: bool = True) -> Dict[str, Any]:
    dataset_results: List[Dict[str, Any]] = []
    brotli_available = _brotli_available()

    with tempfile.TemporaryDirectory(prefix="mc_production_validation_") as tmp:
        tmp_root = Path(tmp)
        for spec in _dataset_specs(include_very_large=include_very_large):
            dataset_dir = tmp_root / "datasets" / spec.name
            work_dir = tmp_root / "work" / spec.name
            work_dir.mkdir(parents=True, exist_ok=True)
            _build_dataset(dataset_dir, spec)
            dataset_results.append(_measure_dataset(dataset_dir, spec, work_dir))

    final_verdict = _build_final_verdict(dataset_results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version,
        "compression_levels": {
            "zstd": _ZSTD_LEVEL,
            "gzip": _GZIP_LEVEL,
            "brotli": _BROTLI_LEVEL if brotli_available else None,
        },
        "memory_measurement": "Python tracemalloc peak per method",
        "available_memory_mb_at_start": _available_mb(),
        "brotli_available": brotli_available,
        "datasets": dataset_results,
        "correctness_passed": True,
        "determinism_passed": True,
        "final_verdict": final_verdict,
    }

    if output_dir is None:
        output_dir = _RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = _build_markdown_report(dataset_results, final_verdict, brotli_available)
    (output_dir / _JSON_PATH.name).write_text(_json_dumps(payload) + "\n", encoding="utf-8")
    (output_dir / _MARKDOWN_PATH.name).write_text(markdown, encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MetaCompressor production validation.")
    parser.add_argument(
        "--output-dir",
        default=str(_RESULTS_DIR),
        help="Directory for markdown/json results (default: results/).",
    )
    parser.add_argument(
        "--skip-very-large",
        action="store_true",
        help="Skip the 500MB+ corpus.",
    )
    args = parser.parse_args()

    try:
        payload = run_validation(
            output_dir=Path(args.output_dir),
            include_very_large=not args.skip_very_large,
        )
    except ValidationError as exc:
        message = "PRODUCTION_EDGE_BLOCKED Reason: %s" % exc
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _JSON_PATH.write_text(
            _json_dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "final_verdict": message,
                    "correctness_passed": False,
                    "determinism_passed": False,
                    "error": str(exc),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _MARKDOWN_PATH.write_text("# MetaCompressor Production Validation Report\n\n%s\n" % message, encoding="utf-8")
        print(message)
        raise SystemExit(1)
    except Exception as exc:
        message = "PRODUCTION_EDGE_BLOCKED Reason: benchmark failed: %s" % exc
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _JSON_PATH.write_text(
            _json_dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "final_verdict": message,
                    "correctness_passed": False,
                    "determinism_passed": False,
                    "error": str(exc),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _MARKDOWN_PATH.write_text("# MetaCompressor Production Validation Report\n\n%s\n" % message, encoding="utf-8")
        print(message)
        raise

    print(payload["final_verdict"])


if __name__ == "__main__":
    main()
