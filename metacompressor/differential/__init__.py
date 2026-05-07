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
from .orchestrator import DifferentialCompressResult, compress_corpus_differential
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

__all__ = [
    # core
    "ChunkFingerprint",
    "Manifest",
    "DiffResult",
    "ReusePlan",
    "build_manifest",
    "diff_manifests",
    "build_reuse_plan",
    # persistence
    "MANIFEST_FILENAME",
    "RECEIPTS_FILENAME",
    "ARCHIVE_FILENAME",
    "save_manifest",
    "load_manifest",
    "save_receipts",
    "load_receipts",
    "save_archive",
    "load_archive",
    # orchestrator
    "DifferentialCompressResult",
    "compress_corpus_differential",
]
