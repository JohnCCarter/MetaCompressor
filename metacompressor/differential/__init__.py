"""Experimental differential orchestration helpers.

This package provides deterministic, fail-closed primitives for reuse planning.
It does not alter MetaCompressor wire format or compression behavior.
"""

from .core import (
    ChunkFingerprint,
    DiffResult,
    Manifest,
    ReusePlan,
    build_manifest,
    build_reuse_plan,
    diff_manifests,
)

__all__ = [
    "ChunkFingerprint",
    "Manifest",
    "DiffResult",
    "ReusePlan",
    "build_manifest",
    "diff_manifests",
    "build_reuse_plan",
]
