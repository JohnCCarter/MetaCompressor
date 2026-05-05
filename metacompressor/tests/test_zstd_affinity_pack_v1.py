"""Tests for ZSTD-affinity experimental packing (optional MCZ1 wire)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import msgpack
import pytest
import zstandard as zstd

from metacompressor.compressor import (
    CHUNKING_CDC,
    CHUNKING_FIXED,
    build_mc1_container,
    compress,
)
from metacompressor.container import (
    _ZSTD_LEVEL,
    MAGIC,
    MAGIC_DIR,
    VERSION,
    VERSION_DIR,
    deserialise,
    deserialise_dir,
    pack_mc1_payload_affinity,
    pack_mc1dir_payload_affinity,
    pack_mc1dir_payload_msgpack,
)
from metacompressor.corpus import (
    build_corpus_container,
    compress_corpus,
    decompress_corpus,
)
from metacompressor.decompressor import decompress
from metacompressor.utils import CHUNK_SIZE
from metacompressor.zstd_affinity_pack_v1 import (
    is_zstd_affinity_v1_payload,
    unpack_mc1dir_payload,
)


def test_default_mc1_payload_is_msgpack_not_mcz1() -> None:
    data = os.urandom(CHUNK_SIZE * 2 + 17)
    mc1 = compress(data, chunking_mode=CHUNKING_FIXED)
    raw = zstd.ZstdDecompressor().decompress(mc1[5:])
    assert not is_zstd_affinity_v1_payload(raw)


def test_deserialise_accepts_mcz1_wire() -> None:
    data = b"xy" * 3000
    c = build_mc1_container(data, chunking_mode=CHUNKING_FIXED)
    raw = pack_mc1_payload_affinity(c)
    assert is_zstd_affinity_v1_payload(raw)
    mc1 = (
        MAGIC + bytes([VERSION]) + zstd.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw)
    )
    assert deserialise(mc1) == c
    assert decompress(mc1) == data


def test_roundtrip_mc1_cdc_msgpack_default() -> None:
    data = b"z" * 9000
    assert decompress(compress(data, chunking_mode=CHUNKING_CDC)) == data


def test_legacy_msgpack_mc1_still_loads() -> None:
    data = b"legacy " * 500
    c = build_mc1_container(data)
    raw_old = msgpack.packb(
        {
            "chunking_mode": c.chunking_mode,
            "chunks": [[cid, c.chunks[cid]] for cid in sorted(c.chunks)],
            "sequence": c.sequence,
            "chunk_size": c.chunk_size,
        },
        use_bin_type=True,
    )
    mc1 = (
        MAGIC
        + bytes([VERSION])
        + zstd.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw_old)
    )
    assert decompress(mc1) == data


@pytest.mark.parametrize("use_delta", [True, False])
def test_mcz1_mc1dir_roundtrip_via_deserialise(tmp_path, use_delta: bool) -> None:
    d = tmp_path / "in"
    d.mkdir()
    (d / "a.txt").write_bytes(b"hello " * 400)
    (d / "b.txt").write_bytes(b"hello " * 400 + b"!")
    c = build_corpus_container(d, use_delta=use_delta)
    raw = pack_mc1dir_payload_affinity(c)
    blob = (
        MAGIC_DIR
        + bytes([VERSION_DIR])
        + zstd.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw)
    )
    assert deserialise_dir(blob) == c


def test_compress_corpus_roundtrip_default_msgpack(tmp_path) -> None:
    d = tmp_path / "in"
    d.mkdir()
    (d / "f.bin").write_bytes(os.urandom(2048))
    arch = compress_corpus(d)
    out = tmp_path / "out"
    decompress_corpus(arch, out)
    assert (out / "f.bin").read_bytes() == (d / "f.bin").read_bytes()


def test_affinity_pack_unpack_matches_container(tmp_path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "c"
        root.mkdir()
        (root / "a.txt").write_bytes(b"x" * 8000)
        c = build_corpus_container(root)
        raw = pack_mc1dir_payload_affinity(c)
        assert raw[:4] == b"MCZ1"
        assert unpack_mc1dir_payload(raw) == c
        mp = pack_mc1dir_payload_msgpack(c)
        assert not is_zstd_affinity_v1_payload(mp)
