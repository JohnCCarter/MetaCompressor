"""Deterministic differential planning primitives for chunk reuse."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

_MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ChunkFingerprint:
    chunk_id: str
    relative_path: str
    chunk_index: int
    size_bytes: int
    chunk_hash: str


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    chunk_size_bytes: int
    chunks: Tuple[ChunkFingerprint, ...]


@dataclass(frozen=True)
class DiffResult:
    reusable_chunks: Tuple[str, ...]
    changed_chunks: Tuple[str, ...]
    added_chunks: Tuple[str, ...]
    deleted_chunks: Tuple[str, ...]
    ambiguous: bool


@dataclass(frozen=True)
class ReusePlan:
    reuse_chunks: Tuple[str, ...]
    rescan_chunks: Tuple[str, ...]
    dropped_chunks: Tuple[str, ...]
    receipt_validated_chunks: Tuple[str, ...]
    fail_closed: bool
    reason: Optional[str]


def _iter_files(root: Path) -> Tuple[Path, ...]:
    return tuple(
        sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda p: p.relative_to(root).as_posix(),
        )
    )


def build_manifest(path: Path, chunk_size_bytes: int = 1024 * 1024) -> Manifest:
    """Build a deterministic chunk manifest from a directory tree."""
    if chunk_size_bytes <= 0:
        raise ValueError("chunk_size_bytes must be > 0")
    root = Path(path)
    files = _iter_files(root)
    chunks = []
    for file_path in files:
        rel = file_path.relative_to(root).as_posix()
        with file_path.open("rb") as fh:
            chunk_index = 0
            while True:
                payload = fh.read(chunk_size_bytes)
                if not payload:
                    break
                chunk_hash = hashlib.sha256(payload).hexdigest()
                chunk_id = f"{rel}::{chunk_index:08d}"
                chunks.append(
                    ChunkFingerprint(
                        chunk_id=chunk_id,
                        relative_path=rel,
                        chunk_index=chunk_index,
                        size_bytes=len(payload),
                        chunk_hash=chunk_hash,
                    )
                )
                chunk_index += 1
    return Manifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        chunk_size_bytes=int(chunk_size_bytes),
        chunks=tuple(chunks),
    )


def _index_chunks(manifest: Manifest) -> Tuple[Dict[str, ChunkFingerprint], bool]:
    indexed: Dict[str, ChunkFingerprint] = {}
    ambiguous = False
    for chunk in manifest.chunks:
        if chunk.chunk_id in indexed:
            ambiguous = True
            continue
        indexed[chunk.chunk_id] = chunk
    return indexed, ambiguous


def diff_manifests(old: Manifest, new: Manifest) -> DiffResult:
    """Diff two manifests and classify chunk-level transitions."""
    old_idx, old_ambiguous = _index_chunks(old)
    new_idx, new_ambiguous = _index_chunks(new)
    ambiguous = bool(old_ambiguous or new_ambiguous)

    old_ids = set(old_idx)
    new_ids = set(new_idx)
    common_ids = old_ids & new_ids

    reusable = []
    changed = []
    for chunk_id in common_ids:
        old_chunk = old_idx[chunk_id]
        new_chunk = new_idx[chunk_id]
        if (
            old_chunk.size_bytes == new_chunk.size_bytes
            and old_chunk.chunk_hash == new_chunk.chunk_hash
        ):
            reusable.append(chunk_id)
        else:
            changed.append(chunk_id)

    added = list(new_ids - old_ids)
    deleted = list(old_ids - new_ids)
    return DiffResult(
        reusable_chunks=tuple(sorted(reusable)),
        changed_chunks=tuple(sorted(changed)),
        added_chunks=tuple(sorted(added)),
        deleted_chunks=tuple(sorted(deleted)),
        ambiguous=ambiguous,
    )


def _receipt_hash_size(entry: Any) -> Optional[Tuple[str, int]]:
    if isinstance(entry, ChunkFingerprint):
        return entry.chunk_hash, int(entry.size_bytes)
    if isinstance(entry, Mapping):
        raw_hash = entry.get("chunk_hash", entry.get("sha256"))
        raw_size = entry.get("size_bytes", entry.get("size"))
        if raw_hash is None or raw_size is None:
            return None
        try:
            return str(raw_hash), int(raw_size)
        except Exception:
            return None
    return None


def build_reuse_plan(
    diff: DiffResult,
    old_receipts: Mapping[str, Any],
    *,
    old_manifest: Optional[Manifest] = None,
) -> ReusePlan:
    """Build fail-closed reuse plan using advisory receipts and hash/size checks."""
    if diff.ambiguous:
        rescan = sorted(
            set(diff.reusable_chunks)
            | set(diff.changed_chunks)
            | set(diff.added_chunks)
        )
        return ReusePlan(
            reuse_chunks=tuple(),
            rescan_chunks=tuple(rescan),
            dropped_chunks=tuple(sorted(diff.deleted_chunks)),
            receipt_validated_chunks=tuple(),
            fail_closed=True,
            reason="ambiguous_manifest",
        )

    old_idx: Dict[str, ChunkFingerprint] = {}
    if old_manifest is not None:
        old_idx, _ = _index_chunks(old_manifest)

    reuse = []
    rescan = set(diff.changed_chunks) | set(diff.added_chunks)
    validated = []
    fail_closed = False
    reason = None

    for chunk_id in diff.reusable_chunks:
        receipt = old_receipts.get(chunk_id)
        parsed = _receipt_hash_size(receipt)
        if parsed is None:
            rescan.add(chunk_id)
            continue
        receipt_hash, receipt_size = parsed
        if old_manifest is not None:
            old_chunk = old_idx.get(chunk_id)
            if old_chunk is None:
                fail_closed = True
                reason = "missing_old_chunk_for_reusable_id"
                break
            if (
                old_chunk.chunk_hash != receipt_hash
                or old_chunk.size_bytes != receipt_size
            ):
                rescan.add(chunk_id)
                continue
        reuse.append(chunk_id)
        validated.append(chunk_id)

    if fail_closed:
        rescan = (
            set(diff.reusable_chunks)
            | set(diff.changed_chunks)
            | set(diff.added_chunks)
        )
        return ReusePlan(
            reuse_chunks=tuple(),
            rescan_chunks=tuple(sorted(rescan)),
            dropped_chunks=tuple(sorted(diff.deleted_chunks)),
            receipt_validated_chunks=tuple(),
            fail_closed=True,
            reason=reason,
        )

    return ReusePlan(
        reuse_chunks=tuple(sorted(reuse)),
        rescan_chunks=tuple(sorted(rescan)),
        dropped_chunks=tuple(sorted(diff.deleted_chunks)),
        receipt_validated_chunks=tuple(sorted(validated)),
        fail_closed=False,
        reason=None,
    )
