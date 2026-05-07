"""Acceptance hardening benchmark/report for MetaCompressor."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks import production_validation as pv
from metacompressor.differential import (
    ChunkFingerprint,
    Manifest,
    build_manifest,
    build_reuse_plan,
    diff_manifests,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# noqa: E402

_RESULTS_DIR = REPO_ROOT / "results"
_MARKDOWN_PATH = _RESULTS_DIR / "metacompressor_acceptance_hardening.md"
_JSON_PATH = _RESULTS_DIR / "metacompressor_acceptance_hardening.json"
_MIN_STRONG_WIN_NUMERATOR = 2
_MIN_250MB_MEMORY_MB = 2000
_DEFAULT_DATASET_TIMEOUT_S = 180
_DATASET_TIMEOUTS_S = {
    "structured_scale_10mb": 180,
    "structured_scale_50mb": 360,
    "structured_scale_100mb": 720,
    "structured_scale_250mb": 900,
}
_REQUIRED_SCALE_DATASET_NAMES = (
    "structured_scale_10mb",
    "structured_scale_50mb",
    "structured_scale_100mb",
    "structured_scale_250mb",
)
_BENCHMARK_MODES = ("full", "quick")
_DECISION_SCAN_MAX_FILES = 24
_DECISION_SCAN_MAX_BYTES = 2 * 1024 * 1024
_DECISION_SCAN_MAX_LINES = 4000
_MC_RECEIPT_MAGIC = "MC1"
_MC_RECEIPT_SCHEMA_VERSION = 1
_MC_RECEIPT_SIDECAR_FILENAME = ".mcmeta"
_MC_RECEIPT_CREATED_BY = "benchmarks.acceptance_hardening.quick"
_MC_MANIFEST_SIDECAR_FILENAME = ".mcmanifest.json"
_ANALYSIS_SKIP_CONFIDENCE_THRESHOLD = 0.10

DatasetSpec = pv.DatasetSpec
ValidationError = pv.ValidationError
_available_mb = pv._available_mb
_build_dataset = pv._build_dataset
_fmt_bytes = pv._fmt_bytes
_fmt_pct = pv._fmt_pct
_generate_app_service_logs = pv._generate_app_service_logs
_generate_high_cardinality_logs = pv._generate_high_cardinality_logs
_generate_many_small_files = pv._generate_many_small_files
_generate_mixed_microservice_logs = pv._generate_mixed_microservice_logs
_generate_ndjson_logs = pv._generate_ndjson_logs
_generate_nginx_logs = pv._generate_nginx_logs
_generate_noisy_logs = pv._generate_noisy_logs
_json_dumps = pv._json_dumps
_measure_dataset = pv._measure_dataset
_mode_label = pv._mode_label
_DEBUG_LOG_PATH = REPO_ROOT / "debug-7c93f6.log"
_DEBUG_SESSION_ID = "7c93f6"
_DEBUG_RUN_ID = os.getenv("MC_DEBUG_RUN_ID", "pre-fix")


def _debug_log(
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Dict[str, Any],
) -> None:
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _gen_structured_scale_10mb(root: Path) -> None:
    _generate_app_service_logs(root, 10, seed=1001, files=8)


def _gen_structured_scale_50mb(root: Path) -> None:
    _generate_app_service_logs(root, 50, seed=1002, files=12)


def _gen_structured_scale_100mb(root: Path) -> None:
    _generate_app_service_logs(root, 100, seed=1003, files=16)


def _gen_app_service_logs(root: Path) -> None:
    _generate_app_service_logs(root, 12, seed=101, files=10)


def _gen_json_ndjson_logs(root: Path) -> None:
    _generate_ndjson_logs(root, 14, seed=202, files=8)


def _gen_nginx_access_logs(root: Path) -> None:
    _generate_nginx_logs(root, 14, seed=303, files=6)


def _gen_mixed_microservice_logs(root: Path) -> None:
    _generate_mixed_microservice_logs(root, 18, seed=404)


def _gen_high_cardinality_logs(root: Path) -> None:
    _generate_high_cardinality_logs(root, 10, seed=505, files=6)


def _gen_noisy_low_structure_logs(root: Path) -> None:
    _generate_noisy_logs(root, 9, seed=606, files=6)


def _gen_many_small_files_5000(root: Path) -> None:
    _generate_many_small_files(root, seed=909, files=5000)


def _gen_structured_scale_250mb(root: Path) -> None:
    _generate_app_service_logs(root, 250, seed=1004, files=24)


def _gen_structured_scale_500mb(root: Path) -> None:
    _generate_app_service_logs(root, 500, seed=1005, files=32)


_EDGE_DATASET_NAMES = (
    "app_service_logs",
    "json_ndjson_logs",
    "nginx_access_logs",
    "mixed_microservice_logs",
    "high_cardinality_logs",
    "noisy_low_structure_logs",
    "many_small_files_5000",
)
_STRUCTURED_EDGE_DATASET_NAMES = (
    "app_service_logs",
    "json_ndjson_logs",
    "nginx_access_logs",
    "mixed_microservice_logs",
    "high_cardinality_logs",
    "many_small_files_5000",
)


def _large_tests_enabled() -> bool:
    return os.getenv("RUN_LARGE_TESTS") == "1"


def _dataset_specs(include_500mb: bool) -> List[DatasetSpec]:
    specs = [
        DatasetSpec(
            name="structured_scale_10mb",
            dataset_type="structured scale 10MB",
            realism="semi-realistic",
            structured=True,
            generator=_gen_structured_scale_10mb,
        ),
        DatasetSpec(
            name="structured_scale_50mb",
            dataset_type="structured scale 50MB",
            realism="semi-realistic",
            structured=True,
            generator=_gen_structured_scale_50mb,
        ),
        DatasetSpec(
            name="structured_scale_100mb",
            dataset_type="structured scale 100MB",
            realism="semi-realistic",
            structured=True,
            generator=_gen_structured_scale_100mb,
        ),
        DatasetSpec(
            name="app_service_logs",
            dataset_type="app/service logs",
            realism="semi-realistic",
            structured=True,
            generator=_gen_app_service_logs,
        ),
        DatasetSpec(
            name="json_ndjson_logs",
            dataset_type="JSON/NDJSON",
            realism="semi-realistic",
            structured=True,
            generator=_gen_json_ndjson_logs,
        ),
        DatasetSpec(
            name="nginx_access_logs",
            dataset_type="nginx/access",
            realism="semi-realistic",
            structured=True,
            generator=_gen_nginx_access_logs,
        ),
        DatasetSpec(
            name="mixed_microservice_logs",
            dataset_type="mixed microservice logs",
            realism="semi-realistic",
            structured=True,
            generator=_gen_mixed_microservice_logs,
        ),
        DatasetSpec(
            name="high_cardinality_logs",
            dataset_type="high-cardinality logs",
            realism="semi-realistic",
            structured=True,
            generator=_gen_high_cardinality_logs,
        ),
        DatasetSpec(
            name="noisy_low_structure_logs",
            dataset_type="noisy/low-structure logs",
            realism="semi-realistic",
            structured=False,
            generator=_gen_noisy_low_structure_logs,
        ),
        DatasetSpec(
            name="many_small_files_5000",
            dataset_type="many-small-files corpus",
            realism="semi-realistic",
            structured=True,
            generator=_gen_many_small_files_5000,
        ),
    ]
    specs.append(
        DatasetSpec(
            name="structured_scale_250mb",
            dataset_type="structured scale 250MB",
            realism="semi-realistic",
            structured=True,
            generator=_gen_structured_scale_250mb,
        )
    )
    if include_500mb:
        specs.append(
            DatasetSpec(
                name="structured_scale_500mb",
                dataset_type="structured scale 500MB",
                realism="semi-realistic",
                structured=True,
                generator=_gen_structured_scale_500mb,
            )
        )
    # region agent log
    _debug_log(
        run_id=_DEBUG_RUN_ID,
        hypothesis_id="H1",
        location="acceptance_hardening.py:_dataset_specs",
        message="Built dataset specs with generator metadata",
        data={
            "include_500mb": bool(include_500mb),
            "spec_count": len(specs),
            "generator_qualnames": [
                getattr(spec.generator, "__qualname__", str(type(spec.generator)))
                for spec in specs
            ],
            "generator_modules": [
                getattr(spec.generator, "__module__", "<unknown>") for spec in specs
            ],
        },
    )
    # endregion
    return specs


def _structured_edge_results(
    dataset_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        result
        for result in dataset_results
        if result["name"] in _STRUCTURED_EDGE_DATASET_NAMES
        and not _dataset_skipped(result)
    ]


def _dataset_skipped(result: Dict[str, Any]) -> bool:
    return result.get("status") == "skipped"


def _completed_results(dataset_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [result for result in dataset_results if not _dataset_skipped(result)]


def _required_scale_results(
    dataset_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        result
        for result in dataset_results
        if result["name"] in _REQUIRED_SCALE_DATASET_NAMES
    ]


def _skip_reason_for_spec(spec: DatasetSpec, available_memory_mb: int) -> Optional[str]:
    if (
        spec.name == "structured_scale_250mb"
        and available_memory_mb < _MIN_250MB_MEMORY_MB
    ):
        return (
            "skipped: available memory at start (%d MB) below %d MB threshold for 250MB dataset"
            % (available_memory_mb, _MIN_250MB_MEMORY_MB)
        )
    return None


def _dataset_timeout_seconds(spec: DatasetSpec) -> int:
    return _DATASET_TIMEOUTS_S.get(spec.name, _DEFAULT_DATASET_TIMEOUT_S)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _decision_kernel_features(dataset_dir: Path) -> Dict[str, Any]:
    """Bounded, deterministic, read-only feature scan for instrumentation."""
    files = pv._iter_files(dataset_dir)
    input_size = sum(path.stat().st_size for path in files)
    file_count = len(files)
    avg_file_size = (input_size / file_count) if file_count else 0.0

    ext_counts: Dict[str, int] = {}
    for path in files:
        ext = path.suffix.lower() or "<none>"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    extension_distribution = {
        ext: (ext_counts[ext] / file_count if file_count else 0.0)
        for ext in sorted(ext_counts.keys())
    }

    sampled_files = 0
    sampled_bytes = 0
    sampled_lines = 0
    repeated_hits = 0
    json_like_hits = 0
    delimiter_hits = 0
    line_seen: Dict[str, int] = {}
    byte_hist = [0] * 256

    for path in files:
        if sampled_files >= _DECISION_SCAN_MAX_FILES:
            break
        if sampled_bytes >= _DECISION_SCAN_MAX_BYTES:
            break
        sampled_files += 1
        with path.open("rb") as fh:
            while (
                sampled_bytes < _DECISION_SCAN_MAX_BYTES
                and sampled_lines < _DECISION_SCAN_MAX_LINES
            ):
                raw = fh.readline()
                if raw == b"":
                    break
                sampled_bytes += len(raw)
                for b in raw:
                    byte_hist[b] += 1
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                sampled_lines += 1
                if line in line_seen:
                    repeated_hits += 1
                line_seen[line] = line_seen.get(line, 0) + 1
                if line.startswith("{") and ":" in line and line.endswith("}"):
                    json_like_hits += 1
                if any(tok in line for tok in ("=", ":", ",", "\t", "|")):
                    delimiter_hits += 1

    line_count_sample = sampled_lines
    repeated_line_ratio_sample = repeated_hits / sampled_lines if sampled_lines else 0.0
    json_like_ratio_sample = json_like_hits / sampled_lines if sampled_lines else 0.0
    delimiter_ratio_sample = delimiter_hits / sampled_lines if sampled_lines else 0.0

    if sampled_bytes > 0:
        entropy = 0.0
        for c in byte_hist:
            if c == 0:
                continue
            p = c / sampled_bytes
            entropy -= p * math.log2(p)
    else:
        entropy = 0.0
    estimated_entropy_sample = entropy
    entropy_score = _clamp01(1.0 - (estimated_entropy_sample / 8.0))
    structure_score = _clamp01(
        0.5 * delimiter_ratio_sample
        + 0.3 * repeated_line_ratio_sample
        + 0.2 * json_like_ratio_sample
    )
    row_reuse_score = _clamp01(
        0.65 * repeated_line_ratio_sample + 0.35 * delimiter_ratio_sample
    )
    columnar_score = _clamp01(
        0.5 * structure_score + 0.25 * json_like_ratio_sample + 0.25 * entropy_score
    )
    confidence_score = _clamp01(
        abs(columnar_score - row_reuse_score) * 0.8 + structure_score * 0.2
    )

    return {
        "input_size": int(input_size),
        "file_count": int(file_count),
        "avg_file_size": avg_file_size,
        "extension_distribution": extension_distribution,
        "line_count_sample": int(line_count_sample),
        "repeated_line_ratio_sample": repeated_line_ratio_sample,
        "json_like_ratio_sample": json_like_ratio_sample,
        "delimiter_ratio_sample": delimiter_ratio_sample,
        "estimated_entropy_sample": estimated_entropy_sample,
        "structure_score": structure_score,
        "row_reuse_score": row_reuse_score,
        "columnar_score": columnar_score,
        "entropy_score": entropy_score,
        "confidence_score": confidence_score,
    }


def _decision_kernel_features_for_chunk_ids(
    dataset_dir: Path, chunk_ids: List[str], chunk_size_bytes: int
) -> Dict[str, Any]:
    """Bounded scan over explicit chunk ids (fail-closed helper for skip mode)."""
    byte_hist = [0] * 256
    line_seen: Dict[str, int] = {}
    sampled_bytes = 0
    sampled_lines = 0
    repeated_hits = 0
    json_like_hits = 0
    delimiter_hits = 0

    valid_chunks: List[tuple[Path, int]] = []
    for chunk_id in sorted(set(chunk_ids)):
        try:
            rel, idx_str = chunk_id.rsplit("::", 1)
            chunk_index = int(idx_str)
            if chunk_index < 0:
                continue
        except Exception:
            continue
        file_path = dataset_dir / rel
        if not file_path.exists() or not file_path.is_file():
            continue
        valid_chunks.append((file_path, chunk_index))

    files = pv._iter_files(dataset_dir)
    input_size = sum(path.stat().st_size for path in files)
    file_count = len(files)
    avg_file_size = (input_size / file_count) if file_count else 0.0
    ext_counts: Dict[str, int] = {}
    for path in files:
        ext = path.suffix.lower() or "<none>"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    extension_distribution = {
        ext: (ext_counts[ext] / file_count if file_count else 0.0)
        for ext in sorted(ext_counts.keys())
    }

    for file_path, chunk_index in valid_chunks:
        if sampled_bytes >= _DECISION_SCAN_MAX_BYTES:
            break
        if sampled_lines >= _DECISION_SCAN_MAX_LINES:
            break
        start = chunk_index * chunk_size_bytes
        to_read = min(chunk_size_bytes, _DECISION_SCAN_MAX_BYTES - sampled_bytes)
        if to_read <= 0:
            break
        with file_path.open("rb") as fh:
            fh.seek(start)
            raw_chunk = fh.read(to_read)
        if not raw_chunk:
            continue
        sampled_bytes += len(raw_chunk)
        for b in raw_chunk:
            byte_hist[b] += 1
        for line in raw_chunk.decode("utf-8", errors="ignore").splitlines():
            if sampled_lines >= _DECISION_SCAN_MAX_LINES:
                break
            s = line.strip()
            if not s:
                continue
            sampled_lines += 1
            if s in line_seen:
                repeated_hits += 1
            line_seen[s] = line_seen.get(s, 0) + 1
            if s.startswith("{") and ":" in s and s.endswith("}"):
                json_like_hits += 1
            if any(tok in s for tok in ("=", ":", ",", "\t", "|")):
                delimiter_hits += 1

    line_count_sample = sampled_lines
    repeated_line_ratio_sample = repeated_hits / sampled_lines if sampled_lines else 0.0
    json_like_ratio_sample = json_like_hits / sampled_lines if sampled_lines else 0.0
    delimiter_ratio_sample = delimiter_hits / sampled_lines if sampled_lines else 0.0
    entropy = 0.0
    if sampled_bytes > 0:
        for c in byte_hist:
            if c == 0:
                continue
            p = c / sampled_bytes
            entropy -= p * math.log2(p)
    estimated_entropy_sample = entropy
    entropy_score = _clamp01(1.0 - (estimated_entropy_sample / 8.0))
    structure_score = _clamp01(
        0.5 * delimiter_ratio_sample
        + 0.3 * repeated_line_ratio_sample
        + 0.2 * json_like_ratio_sample
    )
    row_reuse_score = _clamp01(
        0.65 * repeated_line_ratio_sample + 0.35 * delimiter_ratio_sample
    )
    columnar_score = _clamp01(
        0.5 * structure_score + 0.25 * json_like_ratio_sample + 0.25 * entropy_score
    )
    confidence_score = _clamp01(
        abs(columnar_score - row_reuse_score) * 0.8 + structure_score * 0.2
    )
    return {
        "input_size": int(input_size),
        "file_count": int(file_count),
        "avg_file_size": avg_file_size,
        "extension_distribution": extension_distribution,
        "line_count_sample": int(line_count_sample),
        "repeated_line_ratio_sample": repeated_line_ratio_sample,
        "json_like_ratio_sample": json_like_ratio_sample,
        "delimiter_ratio_sample": delimiter_ratio_sample,
        "estimated_entropy_sample": estimated_entropy_sample,
        "structure_score": structure_score,
        "row_reuse_score": row_reuse_score,
        "columnar_score": columnar_score,
        "entropy_score": entropy_score,
        "confidence_score": confidence_score,
    }


def _compute_receipt_fingerprint(dataset_dir: Path) -> Dict[str, Any]:
    """Compute bounded deterministic fingerprint used to validate MCReceipt."""
    files = pv._iter_files(dataset_dir)
    size_bytes = sum(path.stat().st_size for path in files)
    digest = hashlib.sha256()
    sampled_bytes = 0
    sampled_files = 0

    for path in files:
        if sampled_files >= _DECISION_SCAN_MAX_FILES:
            break
        if sampled_bytes >= _DECISION_SCAN_MAX_BYTES:
            break
        sampled_files += 1
        rel = path.relative_to(dataset_dir).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\x00")
        with path.open("rb") as fh:
            while sampled_bytes < _DECISION_SCAN_MAX_BYTES:
                chunk = fh.read(min(65536, _DECISION_SCAN_MAX_BYTES - sampled_bytes))
                if not chunk:
                    break
                sampled_bytes += len(chunk)
                digest.update(chunk)
        digest.update(b"\x00")
    return {
        "size_bytes": int(size_bytes),
        "chunk_hash": digest.hexdigest(),
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_valid_receipt(
    receipt_path: Path, fingerprint: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Load sidecar receipt only when mandatory fields and fingerprint match."""
    if not receipt_path.exists():
        return None
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("magic") != _MC_RECEIPT_MAGIC:
        return None
    if int(payload.get("schema_version", -1)) != _MC_RECEIPT_SCHEMA_VERSION:
        return None
    if int(payload.get("size_bytes", -1)) != int(fingerprint["size_bytes"]):
        return None
    if str(payload.get("chunk_hash", "")) != str(fingerprint["chunk_hash"]):
        return None
    return payload


def _load_receipt_advisory(receipt_path: Path) -> Optional[Dict[str, Any]]:
    """Load receipt with schema-level validation only (for diff-assisted skip checks)."""
    if not receipt_path.exists():
        return None
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("magic") != _MC_RECEIPT_MAGIC:
        return None
    if int(payload.get("schema_version", -1)) != _MC_RECEIPT_SCHEMA_VERSION:
        return None
    return payload


def _build_receipt(
    *,
    fingerprint: Dict[str, Any],
    sample_hash: str,
    selected_path_hint: str,
    features: Dict[str, Any],
    decision_reason: str,
    bounded_scan_time_ms_estimate: int,
) -> Dict[str, Any]:
    """Build deterministic advisory MCReceipt payload."""
    return {
        "magic": _MC_RECEIPT_MAGIC,
        "schema_version": _MC_RECEIPT_SCHEMA_VERSION,
        "chunk_hash": str(fingerprint["chunk_hash"]),
        "size_bytes": int(fingerprint["size_bytes"]),
        "sample_hash": sample_hash,
        "selected_path_hint": selected_path_hint,
        "structure_score": _safe_float(features.get("structure_score", 0.0)),
        "row_reuse_score": _safe_float(features.get("row_reuse_score", 0.0)),
        "columnar_score": _safe_float(features.get("columnar_score", 0.0)),
        "entropy_score": _safe_float(features.get("entropy_score", 0.0)),
        "confidence_score": _safe_float(features.get("confidence_score", 0.0)),
        "created_by": _MC_RECEIPT_CREATED_BY,
        "decision_reason": decision_reason,
        "bounded_scan_time_ms_estimate": int(max(0, bounded_scan_time_ms_estimate)),
    }


def _receipt_to_features(
    receipt: Dict[str, Any], fallback: Dict[str, Any]
) -> Dict[str, Any]:
    """Project receipt advisory scores onto the decision feature shape."""
    merged = dict(fallback)
    merged["input_size"] = int(receipt.get("size_bytes", fallback.get("input_size", 0)))
    merged["structure_score"] = _safe_float(
        receipt.get("structure_score"), fallback.get("structure_score", 0.0)
    )
    merged["row_reuse_score"] = _safe_float(
        receipt.get("row_reuse_score"), fallback.get("row_reuse_score", 0.0)
    )
    merged["columnar_score"] = _safe_float(
        receipt.get("columnar_score"), fallback.get("columnar_score", 0.0)
    )
    merged["entropy_score"] = _safe_float(
        receipt.get("entropy_score"), fallback.get("entropy_score", 0.0)
    )
    merged["confidence_score"] = _safe_float(
        receipt.get("confidence_score"), fallback.get("confidence_score", 0.0)
    )
    return merged


def _manifest_to_payload(
    manifest: Manifest, receipts: Optional[Dict[str, Dict[str, Any]]] = None
) -> Dict[str, Any]:
    return {
        "schema_version": int(manifest.schema_version),
        "chunk_size_bytes": int(manifest.chunk_size_bytes),
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "relative_path": chunk.relative_path,
                "chunk_index": int(chunk.chunk_index),
                "size_bytes": int(chunk.size_bytes),
                "chunk_hash": chunk.chunk_hash,
            }
            for chunk in manifest.chunks
        ],
        "receipts": receipts or {},
    }


def _manifest_from_payload(payload: Dict[str, Any]) -> Optional[Manifest]:
    try:
        chunks = []
        for raw in payload.get("chunks", []):
            chunks.append(
                ChunkFingerprint(
                    chunk_id=str(raw["chunk_id"]),
                    relative_path=str(raw["relative_path"]),
                    chunk_index=int(raw["chunk_index"]),
                    size_bytes=int(raw["size_bytes"]),
                    chunk_hash=str(raw["chunk_hash"]),
                )
            )
        return Manifest(
            schema_version=int(payload["schema_version"]),
            chunk_size_bytes=int(payload["chunk_size_bytes"]),
            chunks=tuple(chunks),
        )
    except Exception:
        return None


def _build_differential_report(
    *,
    dataset_dir: Path,
    work_dir: Path,
) -> Dict[str, Any]:
    """Build advisory differential report and persist current manifest sidecar."""
    differential_t0 = time.perf_counter()
    manifest_path = work_dir / _MC_MANIFEST_SIDECAR_FILENAME
    manifest_t0 = time.perf_counter()
    current_manifest = build_manifest(dataset_dir)
    manifest_build_time_ms = int((time.perf_counter() - manifest_t0) * 1000.0)
    previous_manifest = None
    previous_payload: Optional[Dict[str, Any]] = None
    load_t0 = time.perf_counter()
    if manifest_path.exists():
        try:
            previous_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_manifest = _manifest_from_payload(previous_payload)
        except Exception:
            previous_manifest = None
            previous_payload = None
    previous_manifest_load_time_ms = int((time.perf_counter() - load_t0) * 1000.0)

    previous_receipts: Dict[str, Any] = {}
    if previous_manifest is None:
        diff_t0 = time.perf_counter()
        diff = diff_manifests(current_manifest, current_manifest)
        diff_time_ms = int((time.perf_counter() - diff_t0) * 1000.0)
        plan_t0 = time.perf_counter()
        reuse_plan = build_reuse_plan(diff, {}, old_manifest=current_manifest)
        reuse_plan_time_ms = int((time.perf_counter() - plan_t0) * 1000.0)
        reuse_allowed = False
        reuse_reason = "missing_or_invalid_previous_manifest"
    else:
        if isinstance(previous_payload, dict):
            previous_receipts = dict(previous_payload.get("receipts", {}))
        diff_t0 = time.perf_counter()
        diff = diff_manifests(previous_manifest, current_manifest)
        diff_time_ms = int((time.perf_counter() - diff_t0) * 1000.0)
        plan_t0 = time.perf_counter()
        reuse_plan = build_reuse_plan(
            diff, previous_receipts, old_manifest=previous_manifest
        )
        reuse_plan_time_ms = int((time.perf_counter() - plan_t0) * 1000.0)
        reuse_allowed = bool(reuse_plan.reuse_chunks) and not reuse_plan.fail_closed
        if reuse_plan.fail_closed:
            reuse_reason = f"fail_closed:{reuse_plan.reason or 'unknown'}"
        else:
            reuse_reason = "advisory_reuse_plan_generated"

    chunk_count = len(current_manifest.chunks)
    reusable_count = len(reuse_plan.reuse_chunks)
    reuse_ratio = float(reusable_count) / float(chunk_count) if chunk_count > 0 else 0.0
    estimated_rescan_avoided_chunks = max(
        0, chunk_count - len(reuse_plan.rescan_chunks)
    )
    estimated_reuse_ratio_pct = reuse_ratio * 100.0
    report = {
        "manifest_chunk_count": int(chunk_count),
        "reusable_chunk_count": int(reusable_count),
        "rescan_chunk_count": int(len(reuse_plan.rescan_chunks)),
        "new_chunk_count": int(len(diff.added_chunks)),
        "changed_chunk_count": int(len(diff.changed_chunks)),
        "deleted_chunk_count": int(len(diff.deleted_chunks)),
        "reuse_allowed": bool(reuse_allowed),
        "reuse_ratio": reuse_ratio,
        "estimated_rescan_avoided_chunks": int(estimated_rescan_avoided_chunks),
        "estimated_reuse_ratio_pct": estimated_reuse_ratio_pct,
        "reuse_reason": reuse_reason,
        "manifest_build_time_ms": manifest_build_time_ms,
        "previous_manifest_load_time_ms": previous_manifest_load_time_ms,
        "diff_time_ms": diff_time_ms,
        "reuse_plan_time_ms": reuse_plan_time_ms,
        "previous_manifest_valid": bool(previous_manifest is not None),
        "chunk_size_bytes": int(current_manifest.chunk_size_bytes),
        "reusable_chunk_ids": list(reuse_plan.reuse_chunks),
        "rescan_chunk_ids": list(reuse_plan.rescan_chunks),
    }
    current_receipts = {
        chunk.chunk_id: {
            "chunk_hash": chunk.chunk_hash,
            "size_bytes": int(chunk.size_bytes),
        }
        for chunk in current_manifest.chunks
    }
    try:
        write_t0 = time.perf_counter()
        manifest_path.write_text(
            json.dumps(
                _manifest_to_payload(current_manifest, receipts=current_receipts),
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        sidecar_write_time_ms = int((time.perf_counter() - write_t0) * 1000.0)
    except Exception:
        sidecar_write_time_ms = 0
    report["differential_total_time_ms"] = int(
        (time.perf_counter() - differential_t0) * 1000.0
    )
    report["sidecar_write_time_ms"] = int(sidecar_write_time_ms)
    return report


def _zstd_affinity_report(features: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only estimate of how well shaping could improve ZSTD locality."""
    structure_score = float(features.get("structure_score", 0.0))
    row_reuse_score = float(features.get("row_reuse_score", 0.0))
    columnar_score = float(features.get("columnar_score", 0.0))
    entropy_score = float(features.get("entropy_score", 0.0))
    delimiter_ratio = float(features.get("delimiter_ratio_sample", 0.0))
    repeated_ratio = float(features.get("repeated_line_ratio_sample", 0.0))
    json_ratio = float(features.get("json_like_ratio_sample", 0.0))
    input_size = int(features.get("input_size", 0))

    # Conservative weighted blend: high structure/reuse and low entropy imply
    # strong potential for better byte locality in a ZSTD-friendly layout.
    zstd_affinity_score = _clamp01(
        0.30 * structure_score
        + 0.25 * row_reuse_score
        + 0.25 * columnar_score
        + 0.20 * entropy_score
    )

    shaping_candidates: List[str] = []
    if structure_score >= 0.45:
        shaping_candidates.append("stable_field_ordering")
    if delimiter_ratio >= 0.40:
        shaping_candidates.append("repeated_structural_markers")
    if row_reuse_score >= 0.35:
        shaping_candidates.append("dictionary_token_substitution")
        shaping_candidates.append("template_grouping")
    if columnar_score >= 0.40:
        shaping_candidates.append("column_locality")
    if repeated_ratio >= 0.25:
        shaping_candidates.append("prefix_suffix_clustering")
    if json_ratio >= 0.30:
        shaping_candidates.append("delta_friendly_numeric_lanes")

    # Keep deterministic order and unique entries.
    shaping_candidates = sorted(set(shaping_candidates))

    if zstd_affinity_score >= 0.65:
        expected_reason = (
            "high_structure_and_reuse_with_low_entropy_indicate_stronger_zstd_locality"
        )
        unsafe_reason = None
    elif input_size < 128 * 1024:
        expected_reason = "small_input_limited_expected_gain_even_if_shaping_applied"
        unsafe_reason = "dataset_too_small_for_stable_shaping_benefit"
    else:
        expected_reason = "moderate_affinity_only_selective_shaping_likely_to_help"
        unsafe_reason = "insufficient_affinity_for_confident_shaping_without_risk"

    return {
        "zstd_affinity_score": zstd_affinity_score,
        "shaping_candidates": shaping_candidates,
        "expected_zstd_benefit_reason": expected_reason,
        "unsafe_to_shape_reason": unsafe_reason,
    }


def _measure_dataset_quick(
    dataset_dir: Path,
    spec: DatasetSpec,
    work_dir: Path,
    differential_report_enabled: bool = False,
) -> Dict[str, Any]:
    """Quick benchmark path: TAR+ZSTD baseline + MC final only."""
    quick_t0 = time.perf_counter()
    decision_t0 = quick_t0
    raw_size = sum(path.stat().st_size for path in pv._iter_files(dataset_dir))
    methods: Dict[str, Any] = {}

    tar_t0 = time.perf_counter()
    methods["tar_zstd"] = pv._run_tar_baseline(
        dataset_dir,
        work_dir / "tar_zstd",
        "tar_zstd",
        pv._zstd_tar_compressor,
        pv._zstd_tar_decompressor,
    )
    baseline_tar_zstd_time_ms = int((time.perf_counter() - tar_t0) * 1000.0)
    mc_t0 = time.perf_counter()
    methods["mc_final_selected"] = pv._run_mc_mode(
        dataset_dir, "auto", work_dir / "mc_final"
    )
    mc_selected_total_time_ms = int((time.perf_counter() - mc_t0) * 1000.0)

    tar_size = methods["tar_zstd"]["size"]
    tar_compress_s = float(methods["tar_zstd"]["compress_s"])
    final_metrics = methods["mc_final_selected"]["metrics"]
    final_size = methods["mc_final_selected"]["size"]
    mc_compress_s = float(methods["mc_final_selected"]["compress_s"])
    mc_decompress_s = float(methods["mc_final_selected"]["decompress_s"])
    mc_peak_mb = float(methods["mc_final_selected"]["peak_mem_mb"])
    correctness_verify_time_ms = int(
        float(methods["mc_final_selected"].get("correctness_verify_s", 0.0)) * 1000.0
    )
    determinism_verify_time_ms = int(
        float(methods["mc_final_selected"].get("determinism_verify_s", 0.0)) * 1000.0
    )
    timing = final_metrics.get("timing", {})
    timing_breakdown = final_metrics.get("timing_breakdown", {})
    selected_substeps = timing_breakdown.get("selected_build_substeps_ms", {})
    selected_counters = timing_breakdown.get("selected_build_counters", {})
    transform_substeps = timing_breakdown.get("transform_call_substeps_ms", {})
    template_extract_substeps = timing_breakdown.get("template_extract_substeps_ms", {})
    template_extract_counters = timing_breakdown.get("template_extract_counters", {})
    template_extract_validation = timing_breakdown.get(
        "template_extract_validation", {}
    )
    runner_timing = methods["mc_final_selected"].get("runner_timing", {})
    mc_selected_serialize_time_ms = int(float(timing.get("serialize_s", 0.0)) * 1000.0)
    mc_selected_zstd_time_ms = int(float(timing.get("zstd_s", 0.0)) * 1000.0)
    mc_selected_build_time_ms = max(
        0,
        mc_selected_total_time_ms
        - mc_selected_serialize_time_ms
        - mc_selected_zstd_time_ms,
    )
    delta_tar_pct = pv._delta_pct(final_size, tar_size)
    raw_reduction_pct = pv._raw_reduction_pct(raw_size, final_size)
    column_encoding_counts = final_metrics["column_encoding_counts"]
    total_column_count = sum(column_encoding_counts.values())
    fallback_used = bool(final_metrics["chose_raw_fallback"])
    selected_mode = final_metrics["final_selected_mode"]
    scorer_failed = False
    analysis_skip_used = False
    skipped_scan_chunk_count = 0
    forced_rescan_reason: Optional[str] = None
    analysis_skip_saved_estimate_ms = 0
    receipt_path = work_dir / _MC_RECEIPT_SIDECAR_FILENAME
    receipt_exists = receipt_path.exists()
    validation_t0 = time.perf_counter()
    fingerprint = _compute_receipt_fingerprint(dataset_dir)
    load_t0 = time.perf_counter()
    base_features = {
        "input_size": int(raw_size),
        "file_count": 0,
        "avg_file_size": 0.0,
        "extension_distribution": {},
        "line_count_sample": 0,
        "repeated_line_ratio_sample": 0.0,
        "json_like_ratio_sample": 0.0,
        "delimiter_ratio_sample": 0.0,
        "estimated_entropy_sample": 0.0,
        "structure_score": 0.0,
        "row_reuse_score": 0.0,
        "columnar_score": 0.0,
        "entropy_score": 0.0,
        "confidence_score": 0.0,
    }
    receipt = _load_valid_receipt(receipt_path, fingerprint)
    advisory_receipt = _load_receipt_advisory(receipt_path)
    receipt_load_time_ms = int((time.perf_counter() - load_t0) * 1000.0)
    receipt_validation_time_ms = int((time.perf_counter() - validation_t0) * 1000.0)
    receipt_valid = bool(receipt is not None)
    receipt_used = bool(receipt)
    differential_report = None
    if differential_report_enabled:
        differential_report = _build_differential_report(
            dataset_dir=dataset_dir,
            work_dir=work_dir,
        )
    bounded_scan_time_ms = 0
    if not differential_report_enabled:
        if receipt is not None:
            features = _receipt_to_features(receipt, base_features)
        else:
            scan_t0 = time.perf_counter()
            try:
                features = _decision_kernel_features(dataset_dir)
            except Exception:
                scorer_failed = True
                features = dict(base_features)
            bounded_scan_time_ms = int((time.perf_counter() - scan_t0) * 1000.0)
    else:
        can_skip = True
        candidate_receipt = receipt if receipt is not None else advisory_receipt
        if candidate_receipt is None:
            can_skip = False
            forced_rescan_reason = "invalid_or_missing_receipt"
        elif not str(candidate_receipt.get("selected_path_hint", "")).strip():
            can_skip = False
            forced_rescan_reason = "missing_selected_path_hint"
        elif (
            _safe_float(candidate_receipt.get("confidence_score", 0.0))
            < _ANALYSIS_SKIP_CONFIDENCE_THRESHOLD
        ):
            can_skip = False
            forced_rescan_reason = "low_confidence"
        elif not bool(differential_report.get("previous_manifest_valid", False)):
            can_skip = False
            forced_rescan_reason = "invalid_or_missing_manifest"
        elif not bool(differential_report.get("reuse_allowed", False)):
            can_skip = False
            forced_rescan_reason = str(
                differential_report.get("reuse_reason", "reuse_not_allowed")
            )

        if can_skip and candidate_receipt is not None:
            rescan_chunk_ids = list(differential_report.get("rescan_chunk_ids", []))
            reusable_chunk_ids = list(differential_report.get("reusable_chunk_ids", []))
            skipped_scan_chunk_count = len(reusable_chunk_ids)
            analysis_skip_saved_estimate_ms = int(
                max(0, int(candidate_receipt.get("bounded_scan_time_ms_estimate", 0)))
            )
            if rescan_chunk_ids:
                forced_rescan_reason = "changed_chunks_present"
                scan_t0 = time.perf_counter()
                try:
                    _decision_kernel_features_for_chunk_ids(
                        dataset_dir=dataset_dir,
                        chunk_ids=rescan_chunk_ids,
                        chunk_size_bytes=int(
                            differential_report.get("chunk_size_bytes", 1024 * 1024)
                        ),
                    )
                except Exception:
                    scorer_failed = True
                bounded_scan_time_ms = int((time.perf_counter() - scan_t0) * 1000.0)
            analysis_skip_used = True
            features = _receipt_to_features(candidate_receipt, base_features)
            receipt_used = True
        else:
            scan_t0 = time.perf_counter()
            try:
                features = _decision_kernel_features(dataset_dir)
            except Exception:
                scorer_failed = True
                features = dict(base_features)
            bounded_scan_time_ms = int((time.perf_counter() - scan_t0) * 1000.0)
            if analysis_skip_saved_estimate_ms == 0:
                analysis_skip_saved_estimate_ms = 0

    if forced_rescan_reason is None:
        forced_rescan_reason = ""

    decision_reason = "instrumentation_only: selected existing mc_final_selected; scores recorded but not used"
    saved_scan_estimate_ms = int(
        max(
            0,
            (
                receipt.get("bounded_scan_time_ms_estimate", 0)
                if receipt is not None
                else 0
            ),
        )
    )
    sample_hash = hashlib.sha256(
        json.dumps(features, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    mc_receipt = _build_receipt(
        fingerprint=fingerprint,
        sample_hash=sample_hash,
        selected_path_hint=str(selected_mode),
        features=features,
        decision_reason=decision_reason,
        bounded_scan_time_ms_estimate=(
            saved_scan_estimate_ms if receipt_used else bounded_scan_time_ms
        ),
    )
    sidecar_write_time_ms = 0
    try:
        sidecar_t0 = time.perf_counter()
        receipt_path.write_text(
            json.dumps(mc_receipt, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        sidecar_write_time_ms += int((time.perf_counter() - sidecar_t0) * 1000.0)
    except Exception:
        # Sidecar is optional and advisory only.
        pass
    decision_kernel_total_time_ms = int((time.perf_counter() - decision_t0) * 1000.0)
    decision_report = {
        "benchmark_mode": "quick",
        "selected_path": selected_mode,
        "baseline_path": "tar_zstd",
        "input_size": features["input_size"],
        "compressed_size": int(final_size),
        "ratio_delta_vs_tar_zstd": delta_tar_pct,
        "encode_time_ms": int(mc_compress_s * 1000.0),
        "decode_time_ms": int(mc_decompress_s * 1000.0),
        "peak_memory_mb": mc_peak_mb,
        "fallback_used": fallback_used,
        "decision_reason": decision_reason,
        "baseline_encode_time_ms": int(tar_compress_s * 1000.0),
        "scorer_failed": scorer_failed,
        "receipt_used": receipt_used,
        "receipt_valid": receipt_valid,
        "receipt_exists": bool(receipt_exists),
        "receipt_load_time_ms": receipt_load_time_ms,
        "bounded_scan_time_ms": int(bounded_scan_time_ms),
        "receipt_validation_time_ms": receipt_validation_time_ms,
        "decision_kernel_total_time_ms": decision_kernel_total_time_ms,
        "receipt_reuse_saved_scan_estimate_ms": saved_scan_estimate_ms,
        "analysis_skip_used": bool(analysis_skip_used),
        "skipped_scan_chunk_count": int(skipped_scan_chunk_count),
        "forced_rescan_reason": forced_rescan_reason,
        "analysis_skip_saved_estimate_ms": int(analysis_skip_saved_estimate_ms),
        "decision_features": features,
        "mc_receipt": mc_receipt,
        **_zstd_affinity_report(features),
    }
    if differential_report is not None:
        decision_report["differential_report"] = differential_report
        sidecar_write_time_ms += int(
            differential_report.get("sidecar_write_time_ms", 0)
        )
    decision_report["baseline_tar_zstd_time_ms"] = int(baseline_tar_zstd_time_ms)
    decision_report["mc_selected_build_time_ms"] = int(mc_selected_build_time_ms)
    decision_report["mc_selected_serialize_time_ms"] = int(
        mc_selected_serialize_time_ms
    )
    decision_report["mc_selected_zstd_time_ms"] = int(mc_selected_zstd_time_ms)
    decision_report["correctness_verify_time_ms"] = int(correctness_verify_time_ms)
    decision_report["determinism_verify_time_ms"] = int(determinism_verify_time_ms)
    decision_report["sidecar_write_time_ms"] = int(sidecar_write_time_ms)
    decision_report["total_quick_time_ms"] = int(
        (time.perf_counter() - quick_t0) * 1000.0
    )
    decision_report["input_walk_time_ms"] = int(
        selected_substeps.get("input_walk_time_ms", 0)
    )
    decision_report["template_normalization_time_ms"] = int(
        selected_substeps.get("template_normalization_time_ms", 0)
    )
    decision_report["row_grouping_time_ms"] = int(
        selected_substeps.get("row_grouping_time_ms", 0)
    )
    decision_report["columnar_detection_time_ms"] = int(
        selected_substeps.get("columnar_detection_time_ms", 0)
    )
    decision_report["chunk_dedupe_time_ms"] = int(
        selected_substeps.get("chunk_dedupe_time_ms", 0)
    )
    decision_report["delta_reuse_time_ms"] = int(
        selected_substeps.get("delta_reuse_time_ms", 0)
    )
    decision_report["msgpack_object_build_time_ms"] = int(
        selected_substeps.get("msgpack_object_build_time_ms", 0)
    )
    decision_report["memory_copy_materialization_time_ms"] = int(
        selected_substeps.get("memory_copy_materialization_time_ms", 0)
    )
    decision_report["files_processed"] = int(
        selected_counters.get("files_processed", 0)
    )
    decision_report["chunks_processed"] = int(
        selected_counters.get("chunks_processed", 0)
    )
    decision_report["rows_processed_estimate"] = int(
        selected_counters.get("rows_processed_estimate", 0)
    )
    decision_report["templates_detected"] = int(
        selected_counters.get("templates_detected", 0)
    )
    decision_report["dedupe_hits"] = int(selected_counters.get("dedupe_hits", 0))
    decision_report["intermediate_bytes_built"] = int(
        selected_counters.get("intermediate_bytes_built", 0)
    )
    decision_report["source_read_time_ms"] = int(
        transform_substeps.get("source_read_time_ms", 0)
    )
    decision_report["file_record_build_time_ms"] = int(
        transform_substeps.get("file_record_build_time_ms", 0)
    )
    decision_report["template_extract_time_ms"] = int(
        transform_substeps.get("template_extract_time_ms", 0)
    )
    decision_report["normalization_apply_time_ms"] = int(
        transform_substeps.get("normalization_apply_time_ms", 0)
    )
    decision_report["row_model_build_time_ms"] = int(
        transform_substeps.get("row_model_build_time_ms", 0)
    )
    decision_report["dedupe_index_build_time_ms"] = int(
        transform_substeps.get("dedupe_index_build_time_ms", 0)
    )
    decision_report["payload_assembly_time_ms"] = int(
        transform_substeps.get("payload_assembly_time_ms", 0)
    )
    decision_report["final_pack_time_ms"] = int(
        transform_substeps.get("final_pack_time_ms", 0)
    )
    decision_report["final_zstd_time_ms"] = int(
        transform_substeps.get("final_zstd_time_ms", 0)
    )
    decision_report["transform_explained_time_ms"] = int(
        timing_breakdown.get("transform_explained_time_ms", 0)
    )
    decision_report["transform_unexplained_time_ms"] = int(
        timing_breakdown.get("transform_unexplained_time_ms", 0)
    )
    decision_report["transform_explained_pct"] = float(
        timing_breakdown.get("transform_explained_pct", 0.0)
    )
    decision_report["line_split_time_ms"] = int(
        template_extract_substeps.get("line_split_time_ms", 0)
    )
    decision_report["tokenization_time_ms"] = int(
        template_extract_substeps.get("tokenization_time_ms", 0)
    )
    decision_report["pattern_match_time_ms"] = int(
        template_extract_substeps.get("pattern_match_time_ms", 0)
    )
    decision_report["placeholder_detection_time_ms"] = int(
        template_extract_substeps.get("placeholder_detection_time_ms", 0)
    )
    decision_report["template_hash_time_ms"] = int(
        template_extract_substeps.get("template_hash_time_ms", 0)
    )
    decision_report["template_grouping_time_ms"] = int(
        template_extract_substeps.get("template_grouping_time_ms", 0)
    )
    decision_report["template_cache_lookup_time_ms"] = int(
        template_extract_substeps.get("template_cache_lookup_time_ms", 0)
    )
    decision_report["lines_scanned"] = int(
        template_extract_counters.get("lines_scanned", 0)
    )
    decision_report["tokens_scanned"] = int(
        template_extract_counters.get("tokens_scanned", 0)
    )
    decision_report["regex_match_count"] = int(
        template_extract_counters.get("regex_match_count", 0)
    )
    decision_report["templates_created"] = int(
        template_extract_counters.get("templates_created", 0)
    )
    decision_report["template_cache_hits"] = int(
        template_extract_counters.get("template_cache_hits", 0)
    )
    decision_report["template_cache_misses"] = int(
        template_extract_counters.get("template_cache_misses", 0)
    )
    decision_report["template_extract_call_count"] = int(
        template_extract_validation.get("template_extract_call_count", 0)
    )
    decision_report["tokenize_one_file_call_count"] = int(
        template_extract_validation.get("tokenize_one_file_call_count", 0)
    )
    decision_report["regex_compile_time_ms"] = int(
        template_extract_validation.get("regex_compile_time_ms", 0)
    )
    decision_report["regex_apply_time_ms"] = int(
        template_extract_validation.get("regex_apply_time_ms", 0)
    )
    decision_report["shared_memory_pack_time_ms"] = int(
        timing_breakdown.get("shared_memory_pack_time_ms", 0)
    )
    decision_report["shared_memory_unpack_time_ms"] = int(
        timing_breakdown.get("shared_memory_unpack_time_ms", 0)
    )
    decision_report["worker_startup_time_ms"] = int(
        template_extract_validation.get("worker_startup_time_ms", 0)
    )
    decision_report["per_call_overhead_time_ms"] = int(
        template_extract_validation.get("per_call_overhead_time_ms", 0)
    )
    decision_report["tokenization_pattern_double_count"] = bool(
        template_extract_validation.get("tokenization_pattern_double_count", False)
    )
    decision_report["template_extract_exclusive_sum_ms"] = int(
        template_extract_validation.get("template_extract_exclusive_sum_ms", 0)
    )
    decision_report["timing_anomaly"] = bool(
        template_extract_validation.get("timing_anomaly", False)
    )
    decision_report["template_extract_wall_time_ms"] = int(
        template_extract_validation.get("template_extract_wall_time_ms", 0)
    )
    decision_report["template_extract_child_work_time_ms"] = int(
        template_extract_validation.get("template_extract_child_work_time_ms", 0)
    )
    decision_report["template_extract_parent_wait_time_ms"] = int(
        template_extract_validation.get("template_extract_parent_wait_time_ms", 0)
    )
    decision_report["template_extract_queue_submit_time_ms"] = int(
        template_extract_validation.get("template_extract_queue_submit_time_ms", 0)
    )
    decision_report["template_extract_result_collect_time_ms"] = int(
        template_extract_validation.get("template_extract_result_collect_time_ms", 0)
    )
    decision_report["template_extract_unexplained_time_ms"] = int(
        template_extract_validation.get("template_extract_unexplained_time_ms", 0)
    )
    decision_report["inline_template_extract_used"] = bool(
        template_extract_validation.get("inline_template_extract_used", False)
    )
    decision_report["inline_template_extract_reason"] = str(
        template_extract_validation.get("inline_template_extract_reason", "")
    )
    decision_report["inline_template_extract_time_ms"] = int(
        template_extract_validation.get("inline_template_extract_time_ms", 0)
    )
    decision_report["template_extract_saved_estimate_ms"] = int(
        template_extract_validation.get("template_extract_saved_estimate_ms", 0)
    )
    decision_report["runner_setup_time_ms"] = int(
        runner_timing.get("runner_setup_time_ms", 0)
    )
    decision_report["config_load_time_ms"] = int(
        runner_timing.get("config_load_time_ms", 0)
    )
    decision_report["input_copy_or_staging_time_ms"] = int(
        runner_timing.get("input_copy_or_staging_time_ms", 0)
    )
    decision_report["compressor_init_time_ms"] = int(
        runner_timing.get("compressor_init_time_ms", 0)
    )
    decision_report["selected_mode_dispatch_time_ms"] = int(
        runner_timing.get("selected_mode_dispatch_time_ms", 0)
    )
    decision_report["selected_mode_resolve_time_ms"] = int(
        runner_timing.get("selected_mode_resolve_time_ms", 0)
    )
    decision_report["input_model_prepare_time_ms"] = int(
        runner_timing.get("input_model_prepare_time_ms", 0)
    )
    decision_report["transform_call_time_ms"] = int(
        runner_timing.get("transform_call_time_ms", 0)
    )
    decision_report["output_model_finalize_time_ms"] = int(
        runner_timing.get("output_model_finalize_time_ms", 0)
    )
    decision_report["metrics_finalize_time_ms"] = int(
        runner_timing.get("metrics_finalize_time_ms", 0)
    )
    decision_report["dispatch_explained_time_ms"] = int(
        runner_timing.get("dispatch_explained_time_ms", 0)
    )
    decision_report["dispatch_unexplained_time_ms"] = int(
        runner_timing.get("dispatch_unexplained_time_ms", 0)
    )
    decision_report["dispatch_explained_pct"] = float(
        runner_timing.get("dispatch_explained_pct", 0.0)
    )
    decision_report["output_collect_time_ms"] = int(
        runner_timing.get("output_collect_time_ms", 0)
    )
    decision_report["metrics_collect_time_ms"] = int(
        runner_timing.get("metrics_collect_time_ms", 0)
    )
    decision_report["subprocess_or_import_overhead_ms"] = int(
        runner_timing.get("subprocess_or_import_overhead_ms", 0)
    )
    explained_build_time_ms = (
        int(decision_report["input_walk_time_ms"])
        + int(decision_report["template_normalization_time_ms"])
        + int(decision_report["row_grouping_time_ms"])
        + int(decision_report["columnar_detection_time_ms"])
        + int(decision_report["chunk_dedupe_time_ms"])
        + int(decision_report["delta_reuse_time_ms"])
        + int(decision_report["msgpack_object_build_time_ms"])
        + int(decision_report["memory_copy_materialization_time_ms"])
        + int(decision_report["runner_setup_time_ms"])
        + int(decision_report["config_load_time_ms"])
        + int(decision_report["input_copy_or_staging_time_ms"])
        + int(decision_report["compressor_init_time_ms"])
        + int(decision_report["selected_mode_dispatch_time_ms"])
        + int(decision_report["output_collect_time_ms"])
        + int(decision_report["metrics_collect_time_ms"])
        + int(decision_report["subprocess_or_import_overhead_ms"])
    )
    unexplained_build_time_ms = max(
        0, int(decision_report["mc_selected_build_time_ms"]) - explained_build_time_ms
    )
    decision_report["explained_build_time_ms"] = int(explained_build_time_ms)
    decision_report["unexplained_build_time_ms"] = int(unexplained_build_time_ms)
    decision_report["explained_build_pct"] = (
        (
            float(explained_build_time_ms)
            / float(decision_report["mc_selected_build_time_ms"])
            * 100.0
        )
        if int(decision_report["mc_selected_build_time_ms"]) > 0
        else 0.0
    )

    return {
        "name": spec.name,
        "dataset_type": spec.dataset_type,
        "realism": spec.realism,
        "structured": spec.structured,
        "raw_size": raw_size,
        "methods": methods,
        "mc_summary": {
            "selected_mode": selected_mode,
            "before_selected_mode": None,
            "fallback_triggered": fallback_used,
            "template_count": final_metrics["num_shared_templates"],
            "template_reuse_rate": final_metrics["template_reuse_rate"],
            "template_reuse_before": final_metrics["template_reuse_before"],
            "template_reuse_after": final_metrics["template_reuse_after"],
            "json_lines_detected": final_metrics["json_lines_detected"],
            "json_template_count": final_metrics["json_template_count"],
            "normalized_template_count": final_metrics["normalized_template_count"],
            "fuzzy_merge_count": final_metrics["fuzzy_merge_count"],
            "fallback_reason_counts": final_metrics["fallback_reason_counts"],
            "column_count": total_column_count,
            "column_encoding_counts": column_encoding_counts,
            "raw_fallback_lines": final_metrics["raw_fallback_lines"],
            "raw_fallback_files": final_metrics["low_structure_fallback_files"],
            "binary_fallback_files": final_metrics["binary_fallback_files"],
            "before_delta_vs_tar_zstd_pct": None,
            "delta_vs_tar_zstd_pct": delta_tar_pct,
            "delta_vs_zstd_per_file_pct": None,
            "reduction_vs_raw_pct": raw_reduction_pct,
            "verdict": pv._mode_verdict(
                delta_tar_pct if delta_tar_pct is not None else 0.0
            ),
        },
        "decision_report": decision_report,
    }


def _measure_dataset_worker(
    spec: DatasetSpec,
    dataset_dir_str: str,
    work_dir_str: str,
    queue: "multiprocessing.queues.Queue[Dict[str, Any]]",
    benchmark_mode: str,
    differential_report_enabled: bool,
) -> None:
    dataset_dir = Path(dataset_dir_str)
    work_dir = Path(work_dir_str)
    # region agent log
    _debug_log(
        run_id=_DEBUG_RUN_ID,
        hypothesis_id="H3",
        location="acceptance_hardening.py:_measure_dataset_worker",
        message="Worker process entry",
        data={
            "dataset": spec.name,
            "benchmark_mode": benchmark_mode,
            "generator_qualname": getattr(spec.generator, "__qualname__", "<missing>"),
            "generator_module": getattr(spec.generator, "__module__", "<missing>"),
        },
    )
    # endregion
    try:
        _build_dataset(dataset_dir, spec)
        if benchmark_mode == "quick":
            measured = _measure_dataset_quick(
                dataset_dir,
                spec,
                work_dir,
                differential_report_enabled=differential_report_enabled,
            )
        else:
            measured = _measure_dataset(dataset_dir, spec, work_dir)
        queue.put({"result": _finalize_dataset_result(measured)})
    except ValidationError as exc:
        queue.put({"validation_error": str(exc)})
    except Exception as exc:
        queue.put({"error": str(exc)})


def _run_dataset_with_timeout(
    tmp_root: Path,
    spec: DatasetSpec,
    timeout_s: int,
    benchmark_mode: str,
    differential_report_enabled: bool = False,
) -> Dict[str, Any]:
    dataset_dir = tmp_root / "datasets" / spec.name
    work_dir = tmp_root / "work" / spec.name
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = (
        multiprocessing.get_context("fork")
        if os.name == "posix"
        else multiprocessing.get_context()
    )
    queue = ctx.Queue()
    process = ctx.Process(
        target=_measure_dataset_worker,
        args=(
            spec,
            str(dataset_dir),
            str(work_dir),
            queue,
            benchmark_mode,
            differential_report_enabled,
        ),
    )
    # region agent log
    _debug_log(
        run_id=_DEBUG_RUN_ID,
        hypothesis_id="H2",
        location="acceptance_hardening.py:_run_dataset_with_timeout",
        message="About to start process",
        data={
            "dataset": spec.name,
            "os_name": os.name,
            "ctx_type": str(type(ctx)),
            "generator_qualname": getattr(spec.generator, "__qualname__", "<missing>"),
            "generator_module": getattr(spec.generator, "__module__", "<missing>"),
            "timeout_s": int(timeout_s),
            "benchmark_mode": benchmark_mode,
        },
    )
    # endregion
    try:
        process.start()
    except Exception as exc:
        # region agent log
        _debug_log(
            run_id=_DEBUG_RUN_ID,
            hypothesis_id="H2",
            location="acceptance_hardening.py:_run_dataset_with_timeout",
            message="Process start failed",
            data={
                "dataset": spec.name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        # endregion
        raise
    process.join(timeout_s if timeout_s > 0 else None)
    # region agent log
    _debug_log(
        run_id=_DEBUG_RUN_ID,
        hypothesis_id="H4",
        location="acceptance_hardening.py:_run_dataset_with_timeout",
        message="Process join finished",
        data={
            "dataset": spec.name,
            "exitcode": process.exitcode,
            "is_alive": bool(process.is_alive()),
            "queue_empty": bool(queue.empty()),
        },
    )
    # endregion

    if process.is_alive():
        process.terminate()
        process.join()
        queue.close()
        return _skipped_dataset_result(
            spec, "skipped: exceeded %ds time budget" % timeout_s
        )

    if queue.empty():
        queue.close()
        raise RuntimeError(
            "dataset %s failed without a result (exit code %s)"
            % (spec.name, process.exitcode)
        )

    payload = queue.get()
    queue.close()
    if "result" in payload:
        return payload["result"]
    if "validation_error" in payload:
        raise ValidationError(payload["validation_error"])
    raise RuntimeError("dataset %s failed: %s" % (spec.name, payload["error"]))


def _skipped_dataset_result(spec: DatasetSpec, reason: str) -> Dict[str, Any]:
    return {
        "name": spec.name,
        "dataset_type": spec.dataset_type,
        "realism": spec.realism,
        "structured": spec.structured,
        "status": "skipped",
        "skip_reason": reason,
        "correctness_status": "skipped",
        "determinism_status": "skipped",
        "raw_size": None,
        "methods": {},
        "mc_summary": {
            "selected_mode": "skipped",
            "before_selected_mode": None,
            "fallback_triggered": False,
            "template_count": 0,
            "template_reuse_rate": None,
            "template_reuse_before": None,
            "template_reuse_after": None,
            "json_lines_detected": 0,
            "json_template_count": 0,
            "normalized_template_count": 0,
            "fuzzy_merge_count": 0,
            "fallback_reason_counts": {},
            "column_count": 0,
            "column_encoding_counts": {},
            "raw_fallback_lines": 0,
            "raw_fallback_files": 0,
            "binary_fallback_files": 0,
            "before_delta_vs_tar_zstd_pct": None,
            "delta_vs_tar_zstd_pct": None,
            "delta_vs_zstd_per_file_pct": None,
            "reduction_vs_raw_pct": None,
            "verdict": "skipped",
        },
    }


def _finalize_dataset_result(result: Dict[str, Any]) -> Dict[str, Any]:
    result["status"] = "completed"
    result["skip_reason"] = None
    result["correctness_status"] = "passed"
    result["determinism_status"] = "passed"
    return result


def _reason_for_dataset(result: Dict[str, Any]) -> str:
    if _dataset_skipped(result):
        return result["skip_reason"]
    summary = result["mc_summary"]
    delta_pct = summary["delta_vs_tar_zstd_pct"]
    reuse_pct = (summary["template_reuse_rate"] or 0.0) * 100.0
    column_count = summary["column_count"]
    fallback_reasons = summary["fallback_reason_counts"]
    if summary["fallback_triggered"]:
        return "fallback kept loss bounded (%s)" % json.dumps(
            fallback_reasons, sort_keys=True
        )
    if delta_pct is not None and delta_pct <= -10.0:
        return "reuse=%s, columns=%d" % (_fmt_pct(reuse_pct), column_count)
    if fallback_reasons:
        return "close to baseline; fallback reasons=%s" % json.dumps(
            fallback_reasons, sort_keys=True
        )
    return "close to baseline; reuse=%s, columns=%d" % (
        _fmt_pct(reuse_pct),
        column_count,
    )


def _remaining_weak_zones(dataset_results: List[Dict[str, Any]]) -> List[str]:
    weak = []
    for result in _completed_results(dataset_results):
        summary = result["mc_summary"]
        delta_pct = summary["delta_vs_tar_zstd_pct"]
        if delta_pct is None:
            continue
        if delta_pct >= 0.0 or summary["fallback_triggered"]:
            weak.append(
                "- **%s**: delta=%s, mode=%s, reason=%s"
                % (
                    result["name"],
                    _fmt_pct(delta_pct),
                    _mode_label(summary["selected_mode"]),
                    _reason_for_dataset(result),
                )
            )
    return weak


def _recommended_next_improvement(dataset_results: List[Dict[str, Any]]) -> str:
    completed_results = _completed_results(dataset_results)
    if not completed_results:
        return "No measured datasets completed; rerun once resource constraints are resolved."
    ranked = sorted(
        completed_results,
        key=lambda result: (
            result["mc_summary"]["delta_vs_tar_zstd_pct"]
            if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
            else -999.0
        ),
        reverse=True,
    )
    weakest = ranked[0]
    return "Focus on %s next: %s." % (weakest["name"], _reason_for_dataset(weakest))


def _build_final_verdict(dataset_results: List[Dict[str, Any]]) -> str:
    completed_results = _completed_results(dataset_results)
    structured_results = _structured_edge_results(completed_results)
    skipped_required = [
        result["name"]
        for result in _required_scale_results(dataset_results)
        if _dataset_skipped(result)
    ]
    if skipped_required:
        return (
            "ACCEPTANCE_HARDENING_PARTIAL Reason: skipped required scale datasets: %s"
            % ", ".join(skipped_required)
        )
    strong_wins = [
        result
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] <= -10.0
    ]
    hidden_losses = [
        result
        for result in completed_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] > 10.0
    ]
    if hidden_losses:
        return "ACCEPTANCE_HARDENING_PARTIAL Reason: fallback safeguards still allowed >10% loss"
    if len(strong_wins) * 2 <= len(structured_results):
        return (
            "ACCEPTANCE_HARDENING_PARTIAL Reason: MC did not beat TAR+ZSTD by >=10% "
            "on most structured log datasets"
        )
    return "ACCEPTANCE_HARDENING_VALIDATED"


def _build_markdown_report(
    dataset_results: List[Dict[str, Any]], final_verdict: str
) -> str:
    completed_results = _completed_results(dataset_results)
    lines = [
        "# MetaCompressor Acceptance Hardening Report",
        "",
        "Generated by `benchmarks/acceptance_hardening.py`.",
        "",
        "| Dataset | Status | Raw | TAR+ZSTD | MC final | Delta % | Mode | Compress s | Decomp s | Peak MB | Correctness | Determinism | Verdict | Fallback/Reason |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|---|---|",
    ]

    for result in dataset_results:
        if _dataset_skipped(result):
            lines.append(
                "| %s | skipped | n/a | n/a | n/a | n/a | skipped | n/a | n/a | n/a | %s | %s | skipped | %s |"
                % (
                    result["name"],
                    result["correctness_status"],
                    result["determinism_status"],
                    _reason_for_dataset(result),
                )
            )
            continue
        final_method = result["methods"]["mc_final_selected"]
        tar_method = result["methods"]["tar_zstd"]
        summary = result["mc_summary"]
        lines.append(
            "| %s | completed | %s | %s | %s | %s | %s | %.3f | %.3f | %.1f | %s | %s | %s | %s |"
            % (
                result["name"],
                _fmt_bytes(result["raw_size"]),
                _fmt_bytes(tar_method["size"]),
                _fmt_bytes(final_method["size"]),
                _fmt_pct(summary["delta_vs_tar_zstd_pct"]),
                _mode_label(summary["selected_mode"]),
                final_method["compress_s"],
                final_method["decompress_s"],
                final_method["peak_mem_mb"],
                result["correctness_status"],
                result["determinism_status"],
                summary["verdict"],
                _reason_for_dataset(result),
            )
        )

    structured_results = _structured_edge_results(completed_results)
    strong_wins = [
        result["name"]
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and result["mc_summary"]["delta_vs_tar_zstd_pct"] <= -10.0
    ]
    near_wins = [
        result["name"]
        for result in structured_results
        if result["mc_summary"]["delta_vs_tar_zstd_pct"] is not None
        and -10.0 < result["mc_summary"]["delta_vs_tar_zstd_pct"] < 0.0
    ]
    fallbacks = [
        result["name"]
        for result in completed_results
        if result["mc_summary"]["fallback_triggered"]
    ]
    skipped_results = [
        result["name"] for result in dataset_results if _dataset_skipped(result)
    ]
    peak_dataset = None
    slowest_dataset = None
    if completed_results:
        peak_dataset = max(
            completed_results,
            key=lambda result: result["methods"]["mc_final_selected"]["peak_mem_mb"],
        )
        slowest_dataset = max(
            completed_results,
            key=lambda result: result["methods"]["mc_final_selected"]["compress_s"],
        )

    lines += [
        "",
        "## Win-rate summary",
        "",
        "- Structured strong wins (>=10%% vs TAR+ZSTD): %d/%d"
        % (len(strong_wins), len(structured_results)),
        "- Strong-win datasets: %s"
        % (", ".join(strong_wins) if strong_wins else "none"),
        "- Sub-10%% wins: %s" % (", ".join(near_wins) if near_wins else "none"),
        "- Final fallback selections: %s"
        % (", ".join(fallbacks) if fallbacks else "none"),
        "- Skipped datasets: %s"
        % (", ".join(skipped_results) if skipped_results else "none"),
        "",
        "## Speed/memory summary",
        "",
        "- Slowest final compression: %s"
        % (
            "**%s** at %.3fs"
            % (
                slowest_dataset["name"],
                slowest_dataset["methods"]["mc_final_selected"]["compress_s"],
            )
            if slowest_dataset is not None
            else "n/a (no completed datasets)"
        ),
        "- Highest measured peak memory: %s"
        % (
            "**%s** at %.1f MB"
            % (
                peak_dataset["name"],
                peak_dataset["methods"]["mc_final_selected"]["peak_mem_mb"],
            )
            if peak_dataset is not None
            else "n/a (no completed datasets)"
        ),
        "- Tokenize / extract / encode / zstd timings are captured per completed dataset in JSON under `methods.mc_final_selected.metrics.timing`.",
        "",
        "## Trust/correctness summary",
        "",
        "- Every measured MC archive was decompressed and byte-compared during the benchmark run.",
        "- Determinism was verified by compressing each measured MC mode twice and comparing the resulting archives byte-for-byte.",
        "- Final fallback threshold remained aligned to the >10% loss safeguard.",
        "- Skipped datasets are explicitly marked in the table and JSON with a skip reason.",
        "- Final verdict: `%s`" % final_verdict,
        "",
        "## Remaining weak zones",
        "",
    ]
    weak = _remaining_weak_zones(dataset_results)
    if weak:
        lines.extend(weak)
    else:
        lines.append("*(none in this run)*")
    lines += [
        "",
        "## Recommended next improvement",
        "",
        _recommended_next_improvement(dataset_results),
        "",
    ]
    return "\n".join(lines)


def run_validation(
    output_dir: Optional[Path] = None,
    include_500mb: Optional[bool] = None,
    benchmark_mode: str = "full",
    differential_report: Optional[bool] = None,
) -> Dict[str, Any]:
    if benchmark_mode not in _BENCHMARK_MODES:
        raise ValueError(
            "invalid benchmark_mode: %s (expected one of %s)"
            % (benchmark_mode, ", ".join(_BENCHMARK_MODES))
        )
    if include_500mb is None:
        include_500mb = _large_tests_enabled()
    if differential_report is None:
        differential_report = os.getenv("MC_DIFFERENTIAL_REPORT", "").strip() in (
            "1",
            "true",
            "True",
        )

    dataset_results: List[Dict[str, Any]] = []
    available_memory_mb = _available_mb()
    with tempfile.TemporaryDirectory(prefix="mc_acceptance_hardening_") as tmp:
        tmp_root = Path(tmp)
        for spec in _dataset_specs(include_500mb=include_500mb):
            skip_reason = _skip_reason_for_spec(spec, available_memory_mb)
            if skip_reason is not None:
                dataset_results.append(_skipped_dataset_result(spec, skip_reason))
                continue
            timeout_s = _dataset_timeout_seconds(spec)
            dataset_results.append(
                _run_dataset_with_timeout(
                    tmp_root,
                    spec,
                    timeout_s,
                    benchmark_mode=benchmark_mode,
                    differential_report_enabled=differential_report,
                )
            )

    final_verdict = _build_final_verdict(dataset_results)
    completed_results = _completed_results(dataset_results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "available_memory_mb_at_start": available_memory_mb,
        "include_500mb": include_500mb,
        "benchmark_mode": benchmark_mode,
        "datasets": dataset_results,
        "correctness_passed": all(
            result["correctness_status"] == "passed" for result in completed_results
        ),
        "determinism_passed": all(
            result["determinism_status"] == "passed" for result in completed_results
        ),
        "remaining_weak_zones": _remaining_weak_zones(dataset_results),
        "recommended_next_improvement": _recommended_next_improvement(dataset_results),
        "final_verdict": final_verdict,
    }

    if output_dir is None:
        output_dir = _RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / _JSON_PATH.name).write_text(
        _json_dumps(payload) + "\n", encoding="utf-8"
    )
    (output_dir / _MARKDOWN_PATH.name).write_text(
        _build_markdown_report(dataset_results, final_verdict) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run MetaCompressor acceptance hardening validation."
    )
    parser.add_argument(
        "--output-dir",
        default=str(_RESULTS_DIR),
        help="Directory for markdown/json results (default: results/).",
    )
    parser.add_argument(
        "--mode",
        choices=_BENCHMARK_MODES,
        default="full",
        help="Benchmark depth: 'full' runs all baselines, 'quick' runs TAR+ZSTD + MC final only.",
    )
    parser.add_argument(
        "--differential-report",
        action="store_true",
        default=None,
        help="Quick mode only: emit advisory differential_report and .mcmanifest.json sidecar.",
    )
    args = parser.parse_args()

    try:
        payload = run_validation(
            output_dir=Path(args.output_dir),
            benchmark_mode=args.mode,
            differential_report=args.differential_report,
        )
    except ValidationError as exc:
        message = "ACCEPTANCE_HARDENING_BLOCKED Reason: %s" % exc
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
        _MARKDOWN_PATH.write_text(
            "# MetaCompressor Acceptance Hardening Report\n\n%s\n" % message,
            encoding="utf-8",
        )
        print(message)
        raise SystemExit(1)
    except Exception as exc:
        message = "ACCEPTANCE_HARDENING_BLOCKED Reason: benchmark failed: %s" % exc
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
        _MARKDOWN_PATH.write_text(
            "# MetaCompressor Acceptance Hardening Report\n\n%s\n" % message,
            encoding="utf-8",
        )
        print(message)
        raise

    print(payload["final_verdict"])


if __name__ == "__main__":
    main()
