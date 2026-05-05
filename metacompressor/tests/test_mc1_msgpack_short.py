"""Short msgpack keys + streaming serialise round-trips."""

from __future__ import annotations

import os

import msgpack

from metacompressor.compressor import compress
from metacompressor.container import (
    pack_mc1dir_payload,
    pack_mc1dir_payload_msgpack,
    serialise_dir,
)
from metacompressor.corpus import (
    build_corpus_container,
    compress_corpus,
    decompress_corpus,
)
from metacompressor.decompressor import decompress
from metacompressor.mc1_msgpack_short import normalise_mc1dir_payload_keys


def test_short_payload_smaller_or_equal_uncompressed(tmp_path) -> None:
    d = tmp_path / "c"
    d.mkdir()
    (d / "a.bin").write_bytes(b"hello " * 800)
    c = build_corpus_container(d)
    short = pack_mc1dir_payload(c)
    long_ = pack_mc1dir_payload_msgpack(c)
    assert len(short) <= len(long_)


def test_normalise_short_keys() -> None:
    raw = msgpack.packb(
        {
            "cs": 4096,
            "c": [[0, b"abcd"]],
            "f": [{"p": "x.txt", "s": [0]}],
        },
        use_bin_type=True,
    )
    p = normalise_mc1dir_payload_keys(msgpack.unpackb(raw, raw=False))
    assert p["chunk_size"] == 4096
    assert p["files"][0]["path"] == "x.txt"


def test_serialise_dir_roundtrip(tmp_path) -> None:
    d = tmp_path / "in"
    d.mkdir()
    (d / "f.txt").write_bytes(os.urandom(3000))
    arch = compress_corpus(d)
    out = tmp_path / "out"
    out.mkdir()
    decompress_corpus(arch, out)
    assert (out / "f.txt").read_bytes() == (d / "f.txt").read_bytes()


def test_compress_decompress_short_wire() -> None:
    data = b"round " * 500
    mc1 = compress(data)
    assert decompress(mc1) == data


def test_serialise_dir_matches_compress_corpus(tmp_path) -> None:
    d = tmp_path / "in"
    d.mkdir()
    (d / "a.txt").write_bytes(b"y" * 2000)
    c = build_corpus_container(d)
    assert serialise_dir(c) == compress_corpus(d)
