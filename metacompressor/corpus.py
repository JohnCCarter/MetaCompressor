"""Corpus (multi-file) compression with a shared chunk dictionary.

All files in the input directory contribute to a single deduplicated chunk
dictionary, so identical blocks that appear across *different* files are stored
only once.  This gives MetaCompressor a decisive advantage over per-file ZSTD
on corpora of structurally similar files (logs, configs, datasets, etc.).

Public API
----------
compress_corpus(input_dir)   -> bytes        (serialised .mc1dir)
decompress_corpus(data, output_dir) -> None  (reconstruct directory tree)
"""

from __future__ import annotations

from pathlib import Path

from metacompressor.container import (
    MC1DirContainer,
    FileEntry,
    serialise_dir,
    deserialise_dir,
)
from metacompressor.delta import delta_encoded_size, find_similar_chunk
from metacompressor.utils import CHUNK_SIZE, chunk_data, hash_chunk


def compress_corpus(input_dir: Path, chunk_size: int = CHUNK_SIZE) -> bytes:
    """Compress all files under *input_dir* into a single .mc1dir byte string.

    Files are walked recursively and stored with their paths relative to
    *input_dir*.  The shared chunk dictionary deduplicates identical blocks
    across all files.

    All files in the corpus are split using the same *chunk_size*, which is
    stored in the archive and applied consistently during decompression.  This
    uniformity is what allows cross-file deduplication: a chunk that appears in
    multiple files maps to the same entry in the shared dictionary regardless
    of which file it came from.

    Parameters
    ----------
    input_dir:
        Root directory to compress.
    chunk_size:
        Size of each chunk in bytes (default: 4096).  Must be the same value
        for compress and decompress; the value is embedded in the archive.

    Returns
    -------
    bytes
        Serialised .mc1dir archive.

    Raises
    ------
    ValueError
        If *input_dir* is not a directory.
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise ValueError(f"Not a directory: {input_dir}")

    hash_to_id: dict[str, int] = {}
    container = MC1DirContainer(chunk_size=chunk_size)
    # Insertion-order list of full-chunk IDs (delta base candidates).
    full_id_order: list[int] = []
    next_id = 0

    # Collect files in a deterministic order (sorted by relative path).
    all_files = sorted(
        p for p in input_dir.rglob("*") if p.is_file()
    )

    for file_path in all_files:
        rel_path = file_path.relative_to(input_dir).as_posix()
        data = file_path.read_bytes()
        sequence: list[int] = []

        for chunk in chunk_data(data, chunk_size):
            h = hash_chunk(chunk)
            if h not in hash_to_id:
                cid = next_id
                next_id += 1
                hash_to_id[h] = cid

                # Attempt delta encoding against recent full chunks.
                delta_result = find_similar_chunk(chunk, container.chunks, full_id_order)
                if delta_result is not None:
                    base_id, diffs = delta_result
                    if delta_encoded_size(diffs) < len(chunk):
                        container.delta_chunks[cid] = (base_id, len(chunk), diffs)
                        sequence.append(cid)
                        continue

                # Fall back to storing the full chunk.
                container.chunks[cid] = chunk
                full_id_order.append(cid)

            sequence.append(hash_to_id[h])

        container.files.append(FileEntry(path=rel_path, sequence=sequence))

    return serialise_dir(container)


def decompress_corpus(data: bytes, output_dir: Path) -> list[str]:
    """Decompress a .mc1dir archive and recreate the directory tree.

    Parameters
    ----------
    data:
        Serialised .mc1dir byte string.
    output_dir:
        Directory to write recovered files into.  Created if absent.

    Returns
    -------
    list[str]
        Relative paths of all files extracted (in archive order).

    Raises
    ------
    ValueError
        If the archive is corrupt or references a missing chunk.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    container = deserialise_dir(data)
    extracted: list[str] = []

    for entry in container.files:
        parts: list[bytes] = []
        for cid in entry.sequence:
            if cid not in container.chunks:
                raise ValueError(
                    f"Corrupt archive: chunk_id {cid} not found in dictionary"
                    f" (file: {entry.path!r})"
                )
            parts.append(container.chunks[cid])

        file_data = b"".join(parts)
        out_path = output_dir / entry.path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(file_data)
        extracted.append(entry.path)

    return extracted
