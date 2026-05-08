from __future__ import annotations

import json
from pathlib import Path

from metacompressor.differential import (
    ChunkFingerprint,
    Manifest,
    load_archive,
    load_manifest,
    load_receipts,
    save_archive,
    save_manifest,
    save_receipts,
)
from metacompressor.differential.core import _MANIFEST_SCHEMA_VERSION
from metacompressor.differential.persistence import (
    _CHUNK_ARTIFACT_SCHEMA_VERSION,
    deterministic_json_dumps,
    load_chunk_artifacts,
    make_chunk_artifact_metadata,
    save_chunk_artifacts,
    validate_chunk_artifact_metadata,
)


def _sample_manifest() -> Manifest:
    return Manifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        chunk_size_bytes=512,
        chunks=(
            ChunkFingerprint(
                chunk_id="a.txt::00000000",
                relative_path="a.txt",
                chunk_index=0,
                size_bytes=512,
                chunk_hash="abc123",
            ),
            ChunkFingerprint(
                chunk_id="b.txt::00000000",
                relative_path="b.txt",
                chunk_index=0,
                size_bytes=256,
                chunk_hash="def456",
            ),
        ),
    )


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = _sample_manifest()
    p = tmp_path / "manifest.json"
    save_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded == m


def test_receipts_roundtrip(tmp_path: Path) -> None:
    receipts = {
        "a.txt::00000000": {"chunk_hash": "abc123", "size_bytes": 512},
        "b.txt::00000000": {"chunk_hash": "def456", "size_bytes": 256},
    }
    p = tmp_path / "receipts.json"
    save_receipts(receipts, p)
    loaded = load_receipts(p)
    assert loaded == receipts


def test_archive_roundtrip(tmp_path: Path) -> None:
    data = b"\x00\x01\x02\x03" * 256
    p = tmp_path / "archive.mc1dir"
    save_archive(data, p)
    loaded = load_archive(p)
    assert loaded == data


def test_load_missing_manifest_returns_none(tmp_path: Path) -> None:
    result = load_manifest(tmp_path / "nonexistent.json")
    assert result is None


def test_load_missing_receipts_returns_empty_dict(tmp_path: Path) -> None:
    result = load_receipts(tmp_path / "nonexistent.json")
    assert result == {}


def test_load_missing_archive_returns_none(tmp_path: Path) -> None:
    result = load_archive(tmp_path / "nonexistent.mc1dir")
    assert result is None


def test_schema_version_mismatch_returns_none(tmp_path: Path) -> None:
    m = _sample_manifest()
    p = tmp_path / "manifest.json"
    save_manifest(m, p)
    raw = json.loads(p.read_text())
    raw["schema_version"] = 9999
    p.write_text(json.dumps(raw))
    assert load_manifest(p) is None


def test_corrupt_json_manifest_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text("not valid json {{{")
    assert load_manifest(p) is None


def test_corrupt_json_receipts_returns_empty_dict(tmp_path: Path) -> None:
    p = tmp_path / "receipts.json"
    p.write_text("not valid json {{{")
    assert load_receipts(p) == {}


def test_receipts_non_dict_returns_empty_dict(tmp_path: Path) -> None:
    p = tmp_path / "receipts.json"
    p.write_text(json.dumps([1, 2, 3]))
    assert load_receipts(p) == {}


def test_chunk_size_preserved_in_manifest(tmp_path: Path) -> None:
    m = Manifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        chunk_size_bytes=131072,
        chunks=(),
    )
    p = tmp_path / "manifest.json"
    save_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded is not None
    assert loaded.chunk_size_bytes == 131072


def test_save_manifest_creates_parent_dirs(tmp_path: Path) -> None:
    m = _sample_manifest()
    p = tmp_path / "nested" / "deep" / "manifest.json"
    save_manifest(m, p)
    assert p.exists()
    assert load_manifest(p) == m


def test_save_archive_creates_parent_dirs(tmp_path: Path) -> None:
    data = b"hello"
    p = tmp_path / "nested" / "archive.mc1dir"
    save_archive(data, p)
    assert load_archive(p) == data


def test_empty_manifest_roundtrip(tmp_path: Path) -> None:
    m = Manifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        chunk_size_bytes=1024,
        chunks=(),
    )
    p = tmp_path / "manifest.json"
    save_manifest(m, p)
    assert load_manifest(p) == m


def test_manifest_missing_required_key_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema_version": _MANIFEST_SCHEMA_VERSION}))
    assert load_manifest(p) is None


def test_chunk_artifact_metadata_validate_ok() -> None:
    metadata = make_chunk_artifact_metadata(
        encoder_version="v1",
        chunk_hash="abc",
        size_bytes=123,
        chunk_size=1024,
        use_delta=True,
        profile_flags=("logs", "generic"),
        path_hint="a.txt::00000000",
        artifact_hash="ff00",
    )
    valid, reason = validate_chunk_artifact_metadata(metadata)
    assert valid is True
    assert reason == "ok"


def test_chunk_artifact_metadata_missing_field_fails() -> None:
    metadata = make_chunk_artifact_metadata(
        encoder_version="v1",
        chunk_hash="abc",
        size_bytes=123,
        chunk_size=1024,
        use_delta=True,
        profile_flags=("logs",),
        path_hint="a.txt::00000000",
        artifact_hash="ff00",
    )
    metadata.pop("artifact_hash")
    valid, reason = validate_chunk_artifact_metadata(metadata)
    assert valid is False
    assert reason == "missing_required_field:artifact_hash"


def test_chunk_artifact_schema_version_mismatch_fails() -> None:
    metadata = make_chunk_artifact_metadata(
        schema_version=999,
        encoder_version="v1",
        chunk_hash="abc",
        size_bytes=123,
        chunk_size=1024,
        use_delta=True,
        profile_flags=("logs",),
        path_hint="a.txt::00000000",
        artifact_hash="ff00",
    )
    valid, reason = validate_chunk_artifact_metadata(
        metadata, expected_schema_version=_CHUNK_ARTIFACT_SCHEMA_VERSION
    )
    assert valid is False
    assert reason == "schema_version_mismatch"


def test_chunk_artifacts_roundtrip(tmp_path: Path) -> None:
    artifacts = {
        "a.txt::00000000": make_chunk_artifact_metadata(
            encoder_version="v1",
            chunk_hash="abc",
            size_bytes=123,
            chunk_size=1024,
            use_delta=False,
            profile_flags=("generic",),
            path_hint="a.txt::00000000",
            artifact_hash="ff00",
        )
    }
    p = tmp_path / "chunk_artifacts.json"
    save_chunk_artifacts(artifacts, p)
    loaded = load_chunk_artifacts(p)
    assert loaded == artifacts


def test_chunk_artifacts_load_invalid_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "chunk_artifacts.json"
    p.write_text("not valid json {{{")
    assert load_chunk_artifacts(p) == {}


def test_deterministic_json_dumps_stable_order() -> None:
    payload_a = {"b": 2, "a": 1}
    payload_b = {"a": 1, "b": 2}
    assert deterministic_json_dumps(payload_a) == deterministic_json_dumps(payload_b)
