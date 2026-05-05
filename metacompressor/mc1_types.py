"""In-memory shapes for .mc1 / .mc1dir (no I/O).

Separated from :mod:`metacompressor.container` so wire codecs can import types
without circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MC1Container:
    chunk_size: int
    chunks: Dict[int, bytes] = field(
        default_factory=dict
    )  # chunk_id → raw bytes (full chunks)
    sequence: List[int] = field(default_factory=list)  # ordered chunk_ids
    # chunk_id → (base_chunk_id, target_len, [[offset, byte], …])
    delta_chunks: Dict[int, tuple] = field(default_factory=dict)
    # CDC parameters (only meaningful when chunking_mode == "cdc")
    chunking_mode: str = "fixed"
    min_chunk_size: Optional[int] = None
    avg_chunk_size: Optional[int] = None
    max_chunk_size: Optional[int] = None
    cdc_mask: Optional[int] = None


@dataclass
class FileEntry:
    """One file stored inside a .mc1dir archive."""

    path: str  # relative POSIX path
    sequence: List[int]  # chunk_ids in original order


@dataclass
class MC1DirContainer:
    chunk_size: int
    chunks: Dict[int, bytes] = field(default_factory=dict)  # shared full chunk dict
    files: List[FileEntry] = field(default_factory=list)  # per-file entries
    # chunk_id → (base_chunk_id, target_len, [[offset, byte], …])
    delta_chunks: Dict[int, tuple] = field(default_factory=dict)
