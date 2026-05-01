"""Intra-chunk delta encoding utilities.

When a new chunk is highly similar to an existing chunk (same length, fraction
of identical bytes exceeds *SIMILARITY_THRESHOLD*), it can be stored as a
*delta* — a compact list of ``[offset, byte_value]`` pairs that describe only
the positions that differ from a reference (base) chunk.  The full chunk can
be reconstructed losslessly by applying those differences on top of the base.

Delta encoding benefits
-----------------------
* Reduces the raw msgpack payload size for near-duplicate chunks.
* The smaller, structured diff list is more compressible by Zstandard than the
  full chunk bytes would be.
* Falls back automatically to raw storage if the delta would be larger.

Public API
----------
similarity(a, b)                   -> float
compute_delta(base, target)        -> list[list[int]]
apply_delta(base, diffs, length)   -> bytes
delta_encoded_size(diffs)          -> int
find_similar_chunk(chunk, chunks, recent_ids, threshold) -> (base_id, diffs) | None
"""

from __future__ import annotations

import msgpack

SIMILARITY_THRESHOLD = 0.80
MAX_CANDIDATES = 64


def similarity(a: bytes, b: bytes) -> float:
    """Return the fraction of byte positions that are identical in *a* and *b*.

    Returns 0.0 if the lengths differ or either argument is empty.
    """
    n = len(a)
    if n == 0 or len(b) != n:
        return 0.0
    return sum(x == y for x, y in zip(a, b)) / n


def compute_delta(base: bytes, target: bytes) -> list[list[int]]:
    """Return ``[[offset, byte_value], …]`` for every position where *target*
    differs from *base*.

    Only positions within ``range(len(target))`` are examined; positions beyond
    ``len(target)`` are not represented (the caller passes *target_len* to
    :func:`apply_delta` to handle truncation).
    """
    return [[i, target[i]] for i in range(len(target)) if target[i] != base[i]]


def apply_delta(base: bytes, diffs: list, target_len: int) -> bytes:
    """Reconstruct a chunk from *base*, a list of *diffs*, and *target_len*.

    Parameters
    ----------
    base:
        The reference chunk bytes.
    diffs:
        A list of ``[offset, byte_value]`` pairs produced by
        :func:`compute_delta`.
    target_len:
        The exact byte length of the original target chunk.  This handles
        the case where the target is shorter than *base* (e.g. the final
        partial chunk of a file).
    """
    result = bytearray(base[:target_len])
    for entry in diffs:
        result[entry[0]] = entry[1]
    return bytes(result)


def delta_encoded_size(diffs: list[list[int]]) -> int:
    """Return the exact msgpack-serialised byte count of *diffs*.

    This is used as the size reference when deciding whether to store a delta
    or fall back to raw bytes.
    """
    return len(msgpack.packb(diffs, use_bin_type=True))


def find_similar_chunk(
    chunk: bytes,
    full_chunks: dict[int, bytes],
    recent_ids: list[int],
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[int, list[list[int]]] | None:
    """Search recently-added full chunks for a delta base for *chunk*.

    The search is limited to the last :data:`MAX_CANDIDATES` IDs in
    *recent_ids* that have the same byte length as *chunk*, iterated from
    most-recent to least-recent so the best structural match is found quickly.

    Parameters
    ----------
    chunk:
        The new chunk to encode.
    full_chunks:
        Mapping of ``chunk_id → bytes`` for all *full* (non-delta) chunks
        stored so far.  Only these can serve as a delta base.
    recent_ids:
        Ordered list of chunk IDs in insertion order (only full-chunk IDs
        should be included).
    threshold:
        Minimum similarity fraction that a candidate must *exceed* (strictly
        greater than) to be considered for delta encoding.  Defaults to
        :data:`SIMILARITY_THRESHOLD`.

    Returns
    -------
    ``(base_chunk_id, diffs)`` if a suitable base is found, else ``None``.
    """
    chunk_len = len(chunk)
    best_sim = threshold
    best_id: int | None = None
    best_diffs: list[list[int]] | None = None

    # Scan the most-recently-added same-size candidates first.
    candidates = [
        cid
        for cid in reversed(recent_ids[-MAX_CANDIDATES:])
        if len(full_chunks.get(cid, b"")) == chunk_len
    ]

    for cid in candidates:
        base = full_chunks[cid]
        sim = similarity(base, chunk)
        if sim > best_sim:
            best_sim = sim
            best_id = cid
            best_diffs = compute_delta(base, chunk)

    if best_id is None:
        return None
    return best_id, best_diffs  # type: ignore[return-value]
