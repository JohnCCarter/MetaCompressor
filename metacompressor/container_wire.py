"""Helpers for inspecting uncompressed .mc1 / .mc1dir ZSTD payloads (tests)."""

from __future__ import annotations

import msgpack


def wire_includes_delta_chunks(uncompressed_payload: bytes) -> bool:
    """Return True if the payload map contains non-empty ``delta_chunks``."""
    try:
        payload = msgpack.unpackb(uncompressed_payload, raw=False)
    except Exception:
        return False
    return "delta_chunks" in payload and len(payload.get("delta_chunks", [])) > 0
