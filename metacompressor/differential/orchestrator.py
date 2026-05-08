"""Differential corpus compression orchestrator in verification mode.

This path always runs a fresh ``compress_corpus()`` call. Cached state is used
only for advisory comparison and reporting gates. Returned bytes are always the
fresh archive from the current run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import msgpack
import zstandard as zstd

from metacompressor.corpus import compress_corpus
from metacompressor.corpus_template import compress_corpus_template_with_metrics
from metacompressor.utils import CHUNK_SIZE

from .core import Manifest, build_manifest, build_reuse_plan, diff_manifests
from .persistence import (
    ARCHIVE_FILENAME,
    CHUNK_ARTIFACTS_FILENAME,
    MANIFEST_FILENAME,
    RECEIPTS_FILENAME,
    load_archive,
    load_chunk_artifacts,
    load_manifest,
    load_receipts,
    make_chunk_artifact_metadata,
    save_archive,
    save_chunk_artifacts,
    save_manifest,
    save_receipts,
    validate_chunk_artifact_metadata,
)

_log = logging.getLogger(__name__)

_CACHE_META_FILENAME = "cache_meta.json"
_ORCHESTRATOR_VERSION = "0.1.0"
_CACHE_META_SCHEMA_VERSION = 1
_MISS_REASON_KEYS = (
    "manifest_changed",
    "chunk_hash_changed",
    "chunk_size_changed",
    "new_chunks",
    "deleted_chunks",
    "receipt_missing",
    "receipt_mismatch",
    "config_mismatch",
    "archive_missing",
    "low_confidence",
    "noisy_entropy_shift",
    "noisy_fail_closed",
    "deterministic_merge_violation",
    "real_decision_metadata_missing",
    "real_decision_metadata_unavailable",
    "strategy_encoding_real_mismatch",
    "byte_parity_mismatch",
    "artifact_missing",
    "artifact_schema_invalid",
    "artifact_hash_mismatch",
    "runtime_strategy_mismatch",
    "runtime_substitution_parity_mismatch",
    "runtime_replay_nondeterministic",
)


@dataclass(frozen=True)
class DifferentialCompressResult:
    archive: bytes
    report: dict


def compress_corpus_differential(
    input_dir: Path,
    cache_dir: Path,
    *,
    chunk_size: int = CHUNK_SIZE,
    use_delta: bool = False,
) -> DifferentialCompressResult:
    """Compress *input_dir* with differential orchestration in verification mode.

    Builds a new manifest and diffs it against any previously saved manifest.
    If all chunks appear unchanged (cache hit candidate), the fresh archive is
    compared against the cached one and the equality result is recorded in the
    returned report. The fresh archive is **always** returned — no cached bytes
    are substituted in this phase.

    All cache state (manifest, receipts, archive, meta) is updated on every
    call so the next run has fresh reference data.
    """
    input_dir = Path(input_dir)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial_reuse_experiment_enabled = _env_enabled(
        os.environ.get("MC_ENABLE_PARTIAL_REUSE_EXPERIMENT")
    )
    runtime_substitution_enabled = _env_enabled(
        os.environ.get("MC_ENABLE_PARTIAL_REUSE_RUNTIME")
    )
    if runtime_substitution_enabled:
        partial_reuse_experiment_enabled = True
    real_decision_metadata = _compute_real_decision_metadata(input_dir)

    new_manifest = build_manifest(input_dir, chunk_size_bytes=chunk_size)

    old_manifest = load_manifest(cache_dir / MANIFEST_FILENAME)
    old_receipts = load_receipts(cache_dir / RECEIPTS_FILENAME)
    old_archive = load_archive(cache_dir / ARCHIVE_FILENAME)
    old_chunk_artifacts = load_chunk_artifacts(cache_dir / CHUNK_ARTIFACTS_FILENAME)
    old_meta = _load_cache_meta(cache_dir / _CACHE_META_FILENAME)

    cache_hit_candidate = False
    fail_closed = False
    reason: Optional[str] = None
    reuse_count = 0
    rescan_count = 0
    miss_reasons = {k: 0 for k in _MISS_REASON_KEYS}
    partial_reuse_opportunity = False
    reusable_but_not_hit_chunks = 0
    strategy_encoding_real_match_pass = True
    byte_identical_parity_pass = True
    deterministic_merge_pass = True
    noisy_fail_closed_pass = True
    real_decision_metadata_used = False
    gates_evaluated = 0
    gates_failed = 0
    runtime_substitution_attempted = False
    runtime_substitution_used = False
    runtime_substitution_fail_reason: Optional[str] = None
    runtime_substitution_candidate_equal_fresh: Optional[bool] = None
    runtime_replay_deterministic: Optional[bool] = None
    runtime_substitution_time_ms = 0
    runtime_validation_overhead_ms = 0
    runtime_reused_chunks = 0
    runtime_rebuilt_chunks = 0
    mismatch_stage = "none"
    mismatch_first_byte_offset = -1
    candidate_size = 0
    fresh_size = 0
    size_delta = 0
    container_metadata_equal: Optional[bool] = None
    payload_order_equal: Optional[bool] = None
    zstd_frame_equal: Optional[bool] = None
    msgpack_structure_equal: Optional[bool] = None
    suspected_global_dependency = False

    if old_manifest is not None and old_archive is not None and old_meta is not None:
        meta_ok = _meta_matches(old_meta, chunk_size, use_delta)
        if not meta_ok:
            reason = "cache_meta_mismatch"
            miss_reasons["config_mismatch"] = 1
        elif old_manifest.chunk_size_bytes != chunk_size:
            reason = "chunk_size_mismatch"
            miss_reasons["config_mismatch"] = 1
        else:
            expected_hash = _manifest_hash(old_manifest)
            if old_meta.get("full_manifest_hash") != expected_hash:
                reason = "manifest_hash_mismatch"
                miss_reasons["config_mismatch"] = 1
            else:
                diff = diff_manifests(old_manifest, new_manifest)
                plan = build_reuse_plan(diff, old_receipts, old_manifest=old_manifest)
                reuse_count = len(plan.reuse_chunks)
                rescan_count = len(plan.rescan_chunks)
                fail_closed = plan.fail_closed
                reason = plan.reason
                if diff.changed_chunks or diff.added_chunks or diff.deleted_chunks:
                    miss_reasons["manifest_changed"] = 1
                if diff.added_chunks:
                    miss_reasons["new_chunks"] = len(diff.added_chunks)
                if diff.deleted_chunks:
                    miss_reasons["deleted_chunks"] = len(diff.deleted_chunks)
                old_idx = {c.chunk_id: c for c in old_manifest.chunks}
                new_idx = {c.chunk_id: c for c in new_manifest.chunks}
                chunk_hash_changed = 0
                chunk_size_changed = 0
                for cid in diff.changed_chunks:
                    old_c = old_idx.get(cid)
                    new_c = new_idx.get(cid)
                    if old_c is None or new_c is None:
                        continue
                    if old_c.chunk_hash != new_c.chunk_hash:
                        chunk_hash_changed += 1
                    if old_c.size_bytes != new_c.size_bytes:
                        chunk_size_changed += 1
                miss_reasons["chunk_hash_changed"] = chunk_hash_changed
                miss_reasons["chunk_size_changed"] = chunk_size_changed
                receipt_missing = 0
                receipt_mismatch = 0
                for cid in diff.reusable_chunks:
                    entry = old_receipts.get(cid)
                    if not isinstance(entry, dict):
                        receipt_missing += 1
                        continue
                    h = str(entry.get("chunk_hash", ""))
                    s = int(entry.get("size_bytes", -1))
                    old_c = old_idx.get(cid)
                    if old_c is None:
                        receipt_mismatch += 1
                        continue
                    if old_c.chunk_hash != h or old_c.size_bytes != s:
                        receipt_mismatch += 1
                miss_reasons["receipt_missing"] = receipt_missing
                miss_reasons["receipt_mismatch"] = receipt_mismatch
                # Verification-only signal: very high rescan share often means entropy/noise shift.
                denom = reuse_count + rescan_count
                if denom > 0 and (rescan_count / denom) >= 0.8:
                    miss_reasons["noisy_entropy_shift"] = 1
                if partial_reuse_experiment_enabled:
                    gates_evaluated += 1
                    if denom > 0 and (rescan_count / denom) >= 0.8:
                        fail_closed = True
                        reason = "noisy_fail_closed"
                        noisy_fail_closed_pass = True
                        miss_reasons["noisy_fail_closed"] += 1
                        gates_failed += 1
                    merge_ok, merge_reason = _validate_simulated_selective_candidate(
                        new_manifest, plan.reuse_chunks, plan.rescan_chunks
                    )
                    if not merge_ok:
                        fail_closed = True
                        reason = merge_reason
                        cache_hit_candidate = False
                        deterministic_merge_pass = False
                        miss_reasons["deterministic_merge_violation"] += 1
                        gates_failed += 1
                if not fail_closed and len(plan.rescan_chunks) == 0:
                    cache_hit_candidate = True
                partial_reuse_opportunity = bool(
                    not cache_hit_candidate and reuse_count > 0
                )
                reusable_but_not_hit_chunks = (
                    reuse_count if not cache_hit_candidate else 0
                )
    else:
        reason = _missing_cache_reason(old_manifest, old_archive, old_meta)
        if old_archive is None:
            miss_reasons["archive_missing"] = 1

    fresh_archive = compress_corpus(
        input_dir, chunk_size=chunk_size, use_delta=use_delta
    )
    fresh_size = len(fresh_archive)

    archives_equal: Optional[bool] = None
    if cache_hit_candidate:
        archives_equal = old_archive == fresh_archive
        _log.info("differential cache hit candidate: archives_equal=%s", archives_equal)

    if partial_reuse_experiment_enabled:
        gates_evaluated += 2
        if real_decision_metadata is None:
            fail_closed = True
            reason = "real_decision_metadata_unavailable"
            miss_reasons["real_decision_metadata_unavailable"] += 1
            gates_failed += 1
        else:
            real_decision_metadata_used = True
            old_real_decision_metadata = _extract_real_decision_metadata(old_meta)
            if old_real_decision_metadata is None:
                fail_closed = True
                reason = "real_decision_metadata_missing"
                miss_reasons["real_decision_metadata_missing"] += 1
                gates_failed += 1
            else:
                strategy_real_match = (
                    old_real_decision_metadata == real_decision_metadata
                )
                strategy_encoding_real_match_pass = bool(strategy_real_match)
                if not strategy_real_match:
                    fail_closed = True
                    reason = "strategy_encoding_real_mismatch"
                    miss_reasons["strategy_encoding_real_mismatch"] += 1
                    gates_failed += 1

        verification_candidate = (
            old_archive if old_archive is not None else fresh_archive
        )
        parity_ok = verification_candidate == fresh_archive
        byte_identical_parity_pass = bool(parity_ok)
        verification_diag = _diagnose_runtime_parity_mismatch(
            verification_candidate, fresh_archive
        )
        mismatch_stage = str(verification_diag["mismatch_stage"])
        mismatch_first_byte_offset = int(
            verification_diag["mismatch_first_byte_offset"]
        )
        candidate_size = int(verification_diag["candidate_size"])
        fresh_size = int(verification_diag["fresh_size"])
        size_delta = int(verification_diag["size_delta"])
        container_metadata_equal = verification_diag["container_metadata_equal"]
        payload_order_equal = verification_diag["payload_order_equal"]
        zstd_frame_equal = verification_diag["zstd_frame_equal"]
        msgpack_structure_equal = verification_diag["msgpack_structure_equal"]
        suspected_global_dependency = bool(
            verification_diag["suspected_global_dependency"]
        )
        if not parity_ok:
            fail_closed = True
            reason = "byte_parity_mismatch"
            miss_reasons["byte_parity_mismatch"] += 1
            gates_failed += 1

        if runtime_substitution_enabled and old_manifest is not None:
            runtime_reused_chunks = int(reuse_count)
            runtime_rebuilt_chunks = int(rescan_count)
            if fail_closed:
                runtime_substitution_attempted = False
                runtime_substitution_used = False
                runtime_substitution_fail_reason = reason
            else:
                runtime_substitution_attempted = True
                t_runtime_start = time.perf_counter()
                (
                    runtime_archive_candidate,
                    runtime_fail_reason,
                    runtime_replay_deterministic,
                ) = _build_runtime_substitution_candidate(
                    input_dir=input_dir,
                    cache_dir=cache_dir,
                    new_manifest=new_manifest,
                    reuse_chunks=(
                        tuple(plan.reuse_chunks) if "plan" in locals() else tuple()
                    ),
                    rescan_chunks=(
                        tuple(plan.rescan_chunks) if "plan" in locals() else tuple()
                    ),
                    old_chunk_artifacts=old_chunk_artifacts,
                    old_receipts=old_receipts,
                    chunk_size=chunk_size,
                    use_delta=use_delta,
                    expected_real_decision_metadata=real_decision_metadata,
                )
                runtime_substitution_time_ms = int(
                    (time.perf_counter() - t_runtime_start) * 1000.0
                )
                runtime_validation_overhead_ms = runtime_substitution_time_ms
                if runtime_fail_reason is not None or runtime_archive_candidate is None:
                    runtime_substitution_fail_reason = runtime_fail_reason
                    fail_closed = True
                    if runtime_fail_reason is not None:
                        reason = runtime_fail_reason
                        if runtime_fail_reason in miss_reasons:
                            miss_reasons[runtime_fail_reason] += 1
                    gates_failed += 1
                    runtime_substitution_used = False
                else:
                    runtime_substitution_candidate_equal_fresh = (
                        runtime_archive_candidate == fresh_archive
                    )
                    if runtime_archive_candidate is not None:
                        diag = _diagnose_runtime_parity_mismatch(
                            runtime_archive_candidate, fresh_archive
                        )
                        mismatch_stage = str(diag["mismatch_stage"])
                        mismatch_first_byte_offset = int(
                            diag["mismatch_first_byte_offset"]
                        )
                        candidate_size = int(diag["candidate_size"])
                        fresh_size = int(diag["fresh_size"])
                        size_delta = int(diag["size_delta"])
                        container_metadata_equal = diag["container_metadata_equal"]
                        payload_order_equal = diag["payload_order_equal"]
                        zstd_frame_equal = diag["zstd_frame_equal"]
                        msgpack_structure_equal = diag["msgpack_structure_equal"]
                        suspected_global_dependency = bool(
                            diag["suspected_global_dependency"]
                        )
                    if not runtime_substitution_candidate_equal_fresh:
                        fail_closed = True
                        reason = "runtime_substitution_parity_mismatch"
                        miss_reasons["runtime_substitution_parity_mismatch"] += 1
                        gates_failed += 1
                        runtime_substitution_used = False
                    elif runtime_replay_deterministic is False:
                        fail_closed = True
                        reason = "runtime_replay_nondeterministic"
                        miss_reasons["runtime_replay_nondeterministic"] += 1
                        gates_failed += 1
                        runtime_substitution_used = False
                    else:
                        runtime_substitution_used = True

    _persist_chunk_artifacts(
        input_dir=input_dir,
        cache_dir=cache_dir,
        manifest=new_manifest,
        chunk_size=chunk_size,
        use_delta=use_delta,
    )
    receipts = _build_receipts(new_manifest)
    save_manifest(new_manifest, cache_dir / MANIFEST_FILENAME)
    save_receipts(receipts, cache_dir / RECEIPTS_FILENAME)
    save_archive(fresh_archive, cache_dir / ARCHIVE_FILENAME)
    _save_cache_meta(
        new_manifest,
        chunk_size,
        use_delta,
        cache_dir / _CACHE_META_FILENAME,
        real_decision_metadata=real_decision_metadata,
    )

    report = {
        "cache_hit_candidate": cache_hit_candidate,
        "archives_equal": archives_equal,
        "fail_closed": fail_closed,
        "reason": reason,
        "reuse_chunk_count": reuse_count,
        "rescan_chunk_count": rescan_count,
        "miss_reasons": miss_reasons,
        "reusable_but_not_hit_chunks": int(reusable_but_not_hit_chunks),
        "partial_reuse_opportunity": bool(partial_reuse_opportunity),
    }
    if partial_reuse_experiment_enabled:
        report.update(
            {
                "partial_reuse_experiment_enabled": True,
                "verification_mode": (
                    "partial_reuse_runtime_experimental"
                    if runtime_substitution_enabled
                    else "partial_reuse_simulation"
                ),
                "returned_archive_source": "fresh_full_build",
                "byte_identical_parity_pass": bool(byte_identical_parity_pass),
                "strategy_encoding_real_match_pass": bool(
                    strategy_encoding_real_match_pass
                ),
                "deterministic_merge_pass": bool(deterministic_merge_pass),
                "noisy_fail_closed_pass": bool(noisy_fail_closed_pass),
                "real_decision_metadata_used": bool(real_decision_metadata_used),
                "gates_evaluated": int(gates_evaluated),
                "gates_failed": int(gates_failed),
                "fallback_reason_counts": dict(miss_reasons),
                "runtime_substitution_enabled": bool(runtime_substitution_enabled),
                "runtime_substitution_attempted": bool(runtime_substitution_attempted),
                "runtime_substitution_used": bool(runtime_substitution_used),
                "runtime_substitution_fail_reason": runtime_substitution_fail_reason,
                "runtime_substitution_candidate_equal_fresh": runtime_substitution_candidate_equal_fresh,
                "runtime_replay_deterministic": runtime_replay_deterministic,
                "runtime_substitution_time_ms": int(runtime_substitution_time_ms),
                "runtime_validation_overhead_ms": int(runtime_validation_overhead_ms),
                "runtime_substitution_reused_chunks": int(runtime_reused_chunks),
                "runtime_substitution_rebuilt_chunks": int(runtime_rebuilt_chunks),
                "mismatch_stage": mismatch_stage,
                "mismatch_first_byte_offset": int(mismatch_first_byte_offset),
                "candidate_size": int(candidate_size),
                "fresh_size": int(fresh_size),
                "size_delta": int(size_delta),
                "artifact_count_reused": int(runtime_reused_chunks),
                "artifact_count_rebuilt": int(runtime_rebuilt_chunks),
                "container_metadata_equal": container_metadata_equal,
                "payload_order_equal": payload_order_equal,
                "zstd_frame_equal": zstd_frame_equal,
                "msgpack_structure_equal": msgpack_structure_equal,
                "suspected_global_dependency": bool(suspected_global_dependency),
            }
        )
    return DifferentialCompressResult(archive=fresh_archive, report=report)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_receipts(manifest: Manifest) -> Dict[str, Any]:
    return {
        chunk.chunk_id: {
            "chunk_hash": chunk.chunk_hash,
            "size_bytes": chunk.size_bytes,
        }
        for chunk in manifest.chunks
    }


def _manifest_hash(manifest: Manifest) -> str:
    """Deterministic SHA-256 of the manifest's canonical JSON representation."""
    payload = json.dumps(
        {
            "schema_version": manifest.schema_version,
            "chunk_size_bytes": manifest.chunk_size_bytes,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "relative_path": c.relative_path,
                    "chunk_index": c.chunk_index,
                    "size_bytes": c.size_bytes,
                    "chunk_hash": c.chunk_hash,
                }
                for c in manifest.chunks
            ],
        },
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _save_cache_meta(
    manifest: Manifest,
    chunk_size: int,
    use_delta: bool,
    path: Path,
    *,
    real_decision_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    meta = {
        "schema_version": _CACHE_META_SCHEMA_VERSION,
        "compressor_version": _ORCHESTRATOR_VERSION,
        "chunk_size_bytes": chunk_size,
        "use_delta": use_delta,
        "full_manifest_hash": _manifest_hash(manifest),
        "real_decision_metadata": real_decision_metadata,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, indent=None, separators=(",", ":")))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_cache_meta(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if data.get("schema_version") != _CACHE_META_SCHEMA_VERSION:
            return None
        return data
    except Exception:
        return None


def _meta_matches(meta: Dict[str, Any], chunk_size: int, use_delta: bool) -> bool:
    return (
        meta.get("compressor_version") == _ORCHESTRATOR_VERSION
        and meta.get("chunk_size_bytes") == chunk_size
        and meta.get("use_delta") == use_delta
    )


def _missing_cache_reason(
    old_manifest: Optional[Any],
    old_archive: Optional[bytes],
    old_meta: Optional[Any],
) -> str:
    if old_manifest is None:
        return "no_cached_manifest"
    if old_archive is None:
        return "no_cached_archive"
    return "no_cached_meta"


def _env_enabled(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip() in {"1", "true", "True", "yes", "on"}


def _validate_simulated_selective_candidate(
    new_manifest: Manifest,
    reuse_chunks: tuple[str, ...],
    rescan_chunks: tuple[str, ...],
) -> tuple[bool, Optional[str]]:
    reuse_set = set(reuse_chunks)
    rescan_set = set(rescan_chunks)
    if reuse_set & rescan_set:
        return False, "deterministic_merge_violation"
    merged = []
    seen = set()
    for chunk in new_manifest.chunks:
        cid = chunk.chunk_id
        if cid in reuse_set or cid in rescan_set:
            if cid in seen:
                return False, "deterministic_merge_violation"
            seen.add(cid)
            merged.append(cid)
    expected = len(reuse_set | rescan_set)
    if len(merged) != expected:
        return False, "deterministic_merge_violation"
    return True, None


def _artifact_file_path(cache_dir: Path, chunk_id: str) -> Path:
    artifact_key = hashlib.sha256(chunk_id.encode("utf-8")).hexdigest()
    return cache_dir / "chunks" / f"{artifact_key}.bin"


def _persist_chunk_artifacts(
    *,
    input_dir: Path,
    cache_dir: Path,
    manifest: Manifest,
    chunk_size: int,
    use_delta: bool,
) -> None:
    file_cache: Dict[str, bytes] = {}
    artifacts: Dict[str, Any] = {}
    for chunk in manifest.chunks:
        rel = chunk.relative_path
        if rel not in file_cache:
            file_cache[rel] = (input_dir / rel).read_bytes()
        data = file_cache[rel]
        start = int(chunk.chunk_index) * int(chunk_size)
        payload = data[start : start + int(chunk.size_bytes)]
        if len(payload) != int(chunk.size_bytes):
            continue
        artifact_hash = hashlib.sha256(payload).hexdigest()
        artifact_path = _artifact_file_path(cache_dir, chunk.chunk_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(payload)
        artifacts[chunk.chunk_id] = make_chunk_artifact_metadata(
            encoder_version=_ORCHESTRATOR_VERSION,
            chunk_hash=chunk.chunk_hash,
            size_bytes=chunk.size_bytes,
            chunk_size=chunk_size,
            use_delta=use_delta,
            profile_flags=["runtime_experimental"],
            path_hint=chunk.chunk_id,
            artifact_hash=artifact_hash,
        )
    save_chunk_artifacts(artifacts, cache_dir / CHUNK_ARTIFACTS_FILENAME)


def _build_runtime_substitution_candidate(
    *,
    input_dir: Path,
    cache_dir: Path,
    new_manifest: Manifest,
    reuse_chunks: Tuple[str, ...],
    rescan_chunks: Tuple[str, ...],
    old_chunk_artifacts: Dict[str, Any],
    old_receipts: Dict[str, Any],
    chunk_size: int,
    use_delta: bool,
    expected_real_decision_metadata: Optional[Dict[str, Any]],
) -> Tuple[Optional[bytes], Optional[str], Optional[bool]]:
    reuse_set = set(reuse_chunks)
    rescan_set = set(rescan_chunks)
    file_cache: Dict[str, bytes] = {}
    by_file: Dict[str, bytearray] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="mc_runtime_substitution_") as tmp:
            tmp_dir = Path(tmp)
            for chunk in new_manifest.chunks:
                if chunk.relative_path not in by_file:
                    by_file[chunk.relative_path] = bytearray()
                if chunk.chunk_id in reuse_set:
                    meta = old_chunk_artifacts.get(chunk.chunk_id)
                    valid, _reason = validate_chunk_artifact_metadata(meta)
                    if not valid:
                        return None, "artifact_schema_invalid", None
                    if str(meta.get("chunk_hash", "")) != str(chunk.chunk_hash):
                        return None, "artifact_hash_mismatch", None
                    if int(meta.get("size_bytes", -1)) != int(chunk.size_bytes):
                        return None, "artifact_schema_invalid", None
                    receipt = old_receipts.get(chunk.chunk_id, {})
                    if not isinstance(receipt, dict):
                        return None, "receipt_missing", None
                    if str(receipt.get("chunk_hash", "")) != str(
                        chunk.chunk_hash
                    ) or int(receipt.get("size_bytes", -1)) != int(chunk.size_bytes):
                        return None, "receipt_mismatch", None
                    artifact_path = _artifact_file_path(cache_dir, chunk.chunk_id)
                    if not artifact_path.exists():
                        return None, "artifact_missing", None
                    payload = artifact_path.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != str(
                        meta.get("artifact_hash", "")
                    ):
                        return None, "artifact_hash_mismatch", None
                    by_file[chunk.relative_path].extend(payload)
                elif chunk.chunk_id in rescan_set:
                    rel = chunk.relative_path
                    if rel not in file_cache:
                        file_cache[rel] = (input_dir / rel).read_bytes()
                    data = file_cache[rel]
                    start = int(chunk.chunk_index) * int(chunk_size)
                    payload = data[start : start + int(chunk.size_bytes)]
                    if len(payload) != int(chunk.size_bytes):
                        return None, "manifest_changed", None
                    by_file[chunk.relative_path].extend(payload)
                else:
                    return None, "manifest_changed", None
            for rel, payload in by_file.items():
                out_path = tmp_dir / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(bytes(payload))
            runtime_archive = compress_corpus(
                tmp_dir, chunk_size=chunk_size, use_delta=use_delta
            )
            replay_archive = compress_corpus(
                tmp_dir, chunk_size=chunk_size, use_delta=use_delta
            )
            replay_deterministic = bool(runtime_archive == replay_archive)
            runtime_real_decision = _compute_real_decision_metadata(tmp_dir)
            if runtime_real_decision != expected_real_decision_metadata:
                return None, "runtime_strategy_mismatch", replay_deterministic
            return runtime_archive, None, replay_deterministic
    except Exception:
        return None, "runtime_replay_nondeterministic", None


def _first_mismatch_offset(a: bytes, b: bytes) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    if len(a) != len(b):
        return limit
    return -1


def _msgpack_structure_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return (
            "dict",
            tuple(
                (
                    str(k),
                    _msgpack_structure_signature(v),
                )
                for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            ),
        )
    if isinstance(value, list):
        return ("list", tuple(_msgpack_structure_signature(v) for v in value))
    if isinstance(value, bytes):
        return ("bytes", len(value))
    return type(value).__name__


def _extract_container_views(data: bytes) -> Dict[str, Any]:
    view: Dict[str, Any] = {
        "magic": bytes(data[:4]) if len(data) >= 4 else b"",
        "version": int(data[4]) if len(data) >= 5 else -1,
        "compressed_payload": bytes(data[5:]) if len(data) > 5 else b"",
        "raw_payload": None,
        "msgpack_obj": None,
        "container_metadata": None,
        "payload_order": None,
    }
    if len(data) < 6:
        return view
    try:
        raw_payload = zstd.ZstdDecompressor().decompress(view["compressed_payload"])
        view["raw_payload"] = raw_payload
        obj = msgpack.unpackb(raw_payload, raw=False)
        view["msgpack_obj"] = obj
        if isinstance(obj, dict):
            files = obj.get("files", [])
            if isinstance(files, list):
                payload_order = []
                for entry in files:
                    if isinstance(entry, dict):
                        payload_order.append(
                            (
                                str(entry.get("path", "")),
                                tuple(int(x) for x in entry.get("sequence", [])),
                            )
                        )
                view["payload_order"] = tuple(payload_order)
            view["container_metadata"] = {
                "magic": view["magic"],
                "version": view["version"],
                "chunk_size": obj.get("chunk_size"),
                "has_chunks_blob": "chunks_blob" in obj,
                "has_chunks_blob_z": "chunks_blob_z" in obj,
                "has_zstd_dict": "zstd_dict" in obj,
                "delta_chunks_count": (
                    len(obj.get("delta_chunks", []))
                    if isinstance(obj.get("delta_chunks", []), list)
                    else -1
                ),
                "files_count": (
                    len(obj.get("files", []))
                    if isinstance(obj.get("files", []), list)
                    else -1
                ),
            }
    except Exception:
        return view
    return view


def _diagnose_runtime_parity_mismatch(candidate: bytes, fresh: bytes) -> Dict[str, Any]:
    candidate_view = _extract_container_views(candidate)
    fresh_view = _extract_container_views(fresh)
    first_mismatch = _first_mismatch_offset(candidate, fresh)
    zstd_frame_equal = (
        candidate_view["compressed_payload"] == fresh_view["compressed_payload"]
    )
    container_metadata_equal = (
        candidate_view["container_metadata"] == fresh_view["container_metadata"]
    )
    payload_order_equal = candidate_view["payload_order"] == fresh_view["payload_order"]
    msgpack_structure_equal = _msgpack_structure_signature(
        candidate_view["msgpack_obj"]
    ) == _msgpack_structure_signature(fresh_view["msgpack_obj"])
    suspected_global_dependency = bool(
        zstd_frame_equal is False
        or container_metadata_equal is False
        or payload_order_equal is False
    )
    mismatch_stage = "none"
    if candidate != fresh:
        if 0 <= first_mismatch < 5:
            mismatch_stage = "container_header"
        elif zstd_frame_equal is False and msgpack_structure_equal is True:
            mismatch_stage = "zstd_frame"
        elif container_metadata_equal is False:
            mismatch_stage = "container_metadata"
        elif payload_order_equal is False:
            mismatch_stage = "payload_order"
        elif msgpack_structure_equal is False:
            mismatch_stage = "msgpack_structure"
        elif suspected_global_dependency:
            mismatch_stage = "archive_global_dependency"
        else:
            mismatch_stage = "unknown_payload"
    return {
        "mismatch_stage": mismatch_stage,
        "mismatch_first_byte_offset": int(first_mismatch),
        "candidate_size": int(len(candidate)),
        "fresh_size": int(len(fresh)),
        "size_delta": int(len(candidate) - len(fresh)),
        "container_metadata_equal": container_metadata_equal,
        "payload_order_equal": payload_order_equal,
        "zstd_frame_equal": zstd_frame_equal,
        "msgpack_structure_equal": msgpack_structure_equal,
        "suspected_global_dependency": suspected_global_dependency,
    }


def _compute_real_decision_metadata(input_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        _, metrics = compress_corpus_template_with_metrics(
            input_dir, structure_v2_enabled=True, compute_legacy_metrics=False
        )
    except Exception:
        return None
    selected_mode = str(metrics.get("final_selected_mode", ""))
    if not selected_mode:
        return None
    raw_enc = metrics.get("column_encoding_counts", {})
    if not isinstance(raw_enc, dict):
        raw_enc = {}
    normalized_enc = {str(k): int(v) for k, v in sorted(raw_enc.items())}
    return {
        "selected_mode": selected_mode,
        "column_encoding_counts": normalized_enc,
    }


def _extract_real_decision_metadata(
    meta: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(meta, dict):
        return None
    value = meta.get("real_decision_metadata")
    if not isinstance(value, dict):
        return None
    selected_mode = str(value.get("selected_mode", ""))
    if not selected_mode:
        return None
    raw_enc = value.get("column_encoding_counts", {})
    if not isinstance(raw_enc, dict):
        return None
    normalized_enc = {str(k): int(v) for k, v in sorted(raw_enc.items())}
    return {
        "selected_mode": selected_mode,
        "column_encoding_counts": normalized_enc,
    }
