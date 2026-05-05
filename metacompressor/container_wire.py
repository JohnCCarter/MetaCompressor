"""Helpers for inspecting uncompressed .mc1 / .mc1dir ZSTD payloads (tests)."""

from __future__ import annotations

from metacompressor.zstd_affinity_pack_v1 import payload_includes_delta_chunks


def wire_includes_delta_chunks(uncompressed_payload: bytes) -> bool:
    """Return True if the wire payload includes non-empty delta chunk data."""
    return payload_includes_delta_chunks(uncompressed_payload)
