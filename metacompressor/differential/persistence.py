"""Disk persistence for differential manifests, receipts, and cached archives."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .core import (
    _MANIFEST_SCHEMA_VERSION,
    ChunkFingerprint,
    Manifest,
)

MANIFEST_FILENAME = "manifest.json"
RECEIPTS_FILENAME = "receipts.json"
ARCHIVE_FILENAME = "archive.mc1dir"
CHUNK_ARTIFACTS_FILENAME = "chunk_artifacts.json"
_CHUNK_ARTIFACT_SCHEMA_VERSION = 1
_CHUNK_ARTIFACT_REQUIRED_FIELDS = (
    "schema_version",
    "encoder_version",
    "chunk_hash",
    "size_bytes",
    "chunk_size",
    "use_delta",
    "profile_flags",
    "path_hint",
    "artifact_hash",
)


def save_manifest(manifest: Manifest, path: Path) -> None:
    """Serialize *manifest* to JSON at *path*, written atomically."""
    payload = {
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
    }
    _atomic_write_text(path, json.dumps(payload, indent=None, separators=(",", ":")))


def load_manifest(path: Path) -> Optional[Manifest]:
    """Load and validate a manifest from *path*.

    Returns ``None`` if the file is missing, unreadable, corrupt, or its
    schema_version does not match the current ``_MANIFEST_SCHEMA_VERSION``.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if data.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            return None
        chunks = tuple(
            ChunkFingerprint(
                chunk_id=c["chunk_id"],
                relative_path=c["relative_path"],
                chunk_index=int(c["chunk_index"]),
                size_bytes=int(c["size_bytes"]),
                chunk_hash=c["chunk_hash"],
            )
            for c in data["chunks"]
        )
        return Manifest(
            schema_version=int(data["schema_version"]),
            chunk_size_bytes=int(data["chunk_size_bytes"]),
            chunks=chunks,
        )
    except FileNotFoundError:
        return None
    except Exception:
        return None


def save_receipts(receipts: Dict[str, Any], path: Path) -> None:
    """Serialize *receipts* dict to JSON at *path*, written atomically."""
    _atomic_write_text(path, json.dumps(receipts, indent=None, separators=(",", ":")))


def load_receipts(path: Path) -> Dict[str, Any]:
    """Load receipts dict from *path*.

    Returns an empty dict if the file is missing, unreadable, or corrupt.
    Receipts are advisory only — failures are always fail-safe.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def save_archive(data: bytes, path: Path) -> None:
    """Write raw archive bytes to *path*, atomically."""
    _atomic_write_bytes(path, data)


def load_archive(path: Path) -> Optional[bytes]:
    """Read raw archive bytes from *path*.

    Returns ``None`` if the file does not exist.
    """
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def deterministic_json_dumps(payload: Any) -> str:
    """Serialize payload deterministically for stable persistence."""
    return json.dumps(payload, sort_keys=True, indent=None, separators=(",", ":"))


def make_chunk_artifact_metadata(
    *,
    encoder_version: str,
    chunk_hash: str,
    size_bytes: int,
    chunk_size: int,
    use_delta: bool,
    profile_flags: Any,
    path_hint: str,
    artifact_hash: str,
    schema_version: int = _CHUNK_ARTIFACT_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """Create normalized per-chunk artifact metadata."""
    if isinstance(profile_flags, (list, tuple, set)):
        normalized_flags = sorted(str(v) for v in profile_flags)
    else:
        normalized_flags = [str(profile_flags)]
    return {
        "schema_version": int(schema_version),
        "encoder_version": str(encoder_version),
        "chunk_hash": str(chunk_hash),
        "size_bytes": int(size_bytes),
        "chunk_size": int(chunk_size),
        "use_delta": bool(use_delta),
        "profile_flags": normalized_flags,
        "path_hint": str(path_hint),
        "artifact_hash": str(artifact_hash),
    }


def validate_chunk_artifact_metadata(
    metadata: Any,
    *,
    expected_schema_version: int = _CHUNK_ARTIFACT_SCHEMA_VERSION,
) -> tuple[bool, str]:
    """Validate chunk artifact metadata and return (pass, reason)."""
    if not isinstance(metadata, dict):
        return False, "metadata_not_dict"
    for field in _CHUNK_ARTIFACT_REQUIRED_FIELDS:
        if field not in metadata:
            return False, f"missing_required_field:{field}"
    if int(metadata.get("schema_version")) != int(expected_schema_version):
        return False, "schema_version_mismatch"
    if not str(metadata.get("encoder_version", "")).strip():
        return False, "invalid_encoder_version"
    if not str(metadata.get("chunk_hash", "")).strip():
        return False, "invalid_chunk_hash"
    if not str(metadata.get("artifact_hash", "")).strip():
        return False, "invalid_artifact_hash"
    try:
        if int(metadata.get("size_bytes")) < 0:
            return False, "invalid_size_bytes"
    except Exception:
        return False, "invalid_size_bytes"
    try:
        if int(metadata.get("chunk_size")) <= 0:
            return False, "invalid_chunk_size"
    except Exception:
        return False, "invalid_chunk_size"
    if not isinstance(metadata.get("use_delta"), bool):
        return False, "invalid_use_delta"
    profile_flags = metadata.get("profile_flags")
    if not isinstance(profile_flags, (list, tuple)):
        return False, "invalid_profile_flags"
    if not str(metadata.get("path_hint", "")).strip():
        return False, "invalid_path_hint"
    return True, "ok"


def save_chunk_artifacts(artifacts: Dict[str, Any], path: Path) -> None:
    """Save per-chunk artifact metadata map to disk atomically."""
    _atomic_write_text(path, deterministic_json_dumps(artifacts))


def load_chunk_artifacts(path: Path) -> Dict[str, Any]:
    """Load per-chunk artifact metadata map (advisory, fail-safe)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
