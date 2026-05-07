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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from metacompressor.corpus import compress_corpus
from metacompressor.corpus_template import compress_corpus_template_with_metrics
from metacompressor.utils import CHUNK_SIZE

from .core import Manifest, build_manifest, build_reuse_plan, diff_manifests
from .persistence import (
    ARCHIVE_FILENAME,
    MANIFEST_FILENAME,
    RECEIPTS_FILENAME,
    load_archive,
    load_manifest,
    load_receipts,
    save_archive,
    save_manifest,
    save_receipts,
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

    new_manifest = build_manifest(input_dir, chunk_size_bytes=chunk_size)

    old_manifest = load_manifest(cache_dir / MANIFEST_FILENAME)
    old_receipts = load_receipts(cache_dir / RECEIPTS_FILENAME)
    old_archive = load_archive(cache_dir / ARCHIVE_FILENAME)
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

    archives_equal: Optional[bool] = None
    if cache_hit_candidate:
        archives_equal = old_archive == fresh_archive
        _log.info("differential cache hit candidate: archives_equal=%s", archives_equal)

    if partial_reuse_experiment_enabled:
        gates_evaluated += 2
        real_decision_metadata = _compute_real_decision_metadata(input_dir)
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
        if not parity_ok:
            fail_closed = True
            reason = "byte_parity_mismatch"
            miss_reasons["byte_parity_mismatch"] += 1
            gates_failed += 1

    receipts = _build_receipts(new_manifest)
    save_manifest(new_manifest, cache_dir / MANIFEST_FILENAME)
    save_receipts(receipts, cache_dir / RECEIPTS_FILENAME)
    save_archive(fresh_archive, cache_dir / ARCHIVE_FILENAME)
    _save_cache_meta(
        new_manifest,
        chunk_size,
        use_delta,
        cache_dir / _CACHE_META_FILENAME,
        real_decision_metadata=_compute_real_decision_metadata(input_dir),
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
                "verification_mode": "partial_reuse_simulation",
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
