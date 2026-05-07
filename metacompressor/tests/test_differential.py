from __future__ import annotations

from pathlib import Path

from metacompressor.differential import (
    build_manifest,
    build_reuse_plan,
    diff_manifests,
)


def _write_dataset(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, payload in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _make_receipts(manifest):
    return {
        chunk.chunk_id: {
            "chunk_hash": chunk.chunk_hash,
            "size_bytes": chunk.size_bytes,
        }
        for chunk in manifest.chunks
    }


def test_identical_dataset_all_chunks_reusable(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_dataset(d1, {"a.txt": b"alpha\n", "b.txt": b"beta\n"})
    _write_dataset(d2, {"a.txt": b"alpha\n", "b.txt": b"beta\n"})
    m1 = build_manifest(d1, chunk_size_bytes=4)
    m2 = build_manifest(d2, chunk_size_bytes=4)
    diff = diff_manifests(m1, m2)
    plan = build_reuse_plan(diff, _make_receipts(m1), old_manifest=m1)
    assert diff.changed_chunks == ()
    assert diff.added_chunks == ()
    assert diff.deleted_chunks == ()
    assert set(plan.reuse_chunks) == {c.chunk_id for c in m2.chunks}
    assert plan.rescan_chunks == ()


def test_one_changed_chunk_only_that_chunk_rescanned(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_dataset(d1, {"x.log": b"AAAAFFFF"})
    _write_dataset(d2, {"x.log": b"AAAAGGGG"})
    m1 = build_manifest(d1, chunk_size_bytes=4)
    m2 = build_manifest(d2, chunk_size_bytes=4)
    diff = diff_manifests(m1, m2)
    plan = build_reuse_plan(diff, _make_receipts(m1), old_manifest=m1)
    assert diff.changed_chunks == ("x.log::00000001",)
    assert diff.reusable_chunks == ("x.log::00000000",)
    assert plan.reuse_chunks == ("x.log::00000000",)
    assert plan.rescan_chunks == ("x.log::00000001",)


def test_added_file_chunks_rescanned(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_dataset(d1, {"a.txt": b"same"})
    _write_dataset(d2, {"a.txt": b"same", "new.txt": b"new-data"})
    m1 = build_manifest(d1, chunk_size_bytes=4)
    m2 = build_manifest(d2, chunk_size_bytes=4)
    diff = diff_manifests(m1, m2)
    plan = build_reuse_plan(diff, _make_receipts(m1), old_manifest=m1)
    assert diff.added_chunks == ("new.txt::00000000", "new.txt::00000001")
    assert set(plan.rescan_chunks) == set(diff.added_chunks)


def test_deleted_file_has_no_reuse_attempt(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_dataset(d1, {"keep.txt": b"keep", "gone.txt": b"gone"})
    _write_dataset(d2, {"keep.txt": b"keep"})
    m1 = build_manifest(d1, chunk_size_bytes=4)
    m2 = build_manifest(d2, chunk_size_bytes=4)
    diff = diff_manifests(m1, m2)
    plan = build_reuse_plan(diff, _make_receipts(m1), old_manifest=m1)
    assert diff.deleted_chunks == ("gone.txt::00000000",)
    assert "gone.txt::00000000" not in plan.reuse_chunks
    assert "gone.txt::00000000" not in plan.rescan_chunks
    assert plan.dropped_chunks == ("gone.txt::00000000",)


def test_hash_mismatch_fails_closed_for_chunk(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    _write_dataset(d1, {"a.txt": b"alpha"})
    _write_dataset(d2, {"a.txt": b"alpha"})
    m1 = build_manifest(d1, chunk_size_bytes=4)
    m2 = build_manifest(d2, chunk_size_bytes=4)
    diff = diff_manifests(m1, m2)
    receipts = _make_receipts(m1)
    first_chunk = m1.chunks[0].chunk_id
    receipts[first_chunk]["chunk_hash"] = "deadbeef"
    plan = build_reuse_plan(diff, receipts, old_manifest=m1)
    assert first_chunk in plan.rescan_chunks
    assert first_chunk not in plan.reuse_chunks


def test_repeated_runs_are_deterministic(tmp_path: Path) -> None:
    d = tmp_path / "dataset"
    _write_dataset(d, {"b.txt": b"bbb", "a.txt": b"aaa"})
    m1 = build_manifest(d, chunk_size_bytes=2)
    m2 = build_manifest(d, chunk_size_bytes=2)
    assert m1 == m2
    diff1 = diff_manifests(m1, m2)
    diff2 = diff_manifests(m1, m2)
    assert diff1 == diff2
    receipts = _make_receipts(m1)
    p1 = build_reuse_plan(diff1, receipts, old_manifest=m1)
    p2 = build_reuse_plan(diff1, receipts, old_manifest=m1)
    assert p1 == p2
