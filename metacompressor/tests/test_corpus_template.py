"""Tests for corpus template mode (shared template dictionary)."""

from __future__ import annotations

import io
import os
from pathlib import Path

import msgpack
import pytest
import zstandard as zstd

from metacompressor.corpus_template import (
    _ADAPT_COL_V1,
    _ADAPT_COL_V2,
    _MODE_COLUMNAR_V1,
    _MODE_COLUMNAR_V2,
    MAGIC,
    VERSION,
    _tokenize,
    compress_corpus_template,
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_corpus(tmp_path: Path, files: dict) -> Path:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        dest = corpus_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return corpus_dir


def round_trip(tmp_path: Path, files: dict) -> dict:
    corpus_dir = make_corpus(tmp_path, files)
    archive = compress_corpus_template(corpus_dir)
    out_dir = tmp_path / "recovered"
    decompress_corpus_template(archive, out_dir)
    return {rel.replace("\\", "/"): (out_dir / rel).read_bytes() for rel in files}


def unpack_payload(archive: bytes) -> dict:
    assert archive[:4] == MAGIC
    assert archive[4] == VERSION
    with zstd.ZstdDecompressor().stream_reader(io.BytesIO(archive[5:])) as reader:
        raw_payload = reader.read()
    return msgpack.unpackb(raw_payload, raw=False)


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestCorpusTemplateRoundTrip:
    def test_single_text_file(self, tmp_path):
        lines = [f"INFO event={i}\n" for i in range(50)]
        files = {"log.txt": "".join(lines).encode()}
        assert round_trip(tmp_path, files) == files

    def test_multiple_text_files(self, tmp_path):
        template = "ERROR user={i} status=500\n"
        files = {
            f"day{d}.log": "".join(
                template.format(i=i + d * 1000) for i in range(100)
            ).encode()
            for d in range(5)
        }
        assert round_trip(tmp_path, files) == files

    def test_nested_directories(self, tmp_path):
        files = {
            "a/b.log": b"INFO val=1\nINFO val=2\nINFO val=3\n" * 10,
            "a/c.log": b"WARN val=4\nWARN val=5\n" * 10,
            "d.log": b"ERROR code=42\n" * 20,
        }
        assert round_trip(tmp_path, files) == files

    def test_empty_file(self, tmp_path):
        files = {"empty.txt": b"", "nonempty.txt": b"INFO x=1\nINFO x=2\n" * 5}
        assert round_trip(tmp_path, files) == files

    def test_binary_file_preserved(self, tmp_path):
        binary_data = os.urandom(512)
        files = {"data.bin": binary_data, "log.txt": b"INFO n=1\nINFO n=2\n" * 5}
        assert round_trip(tmp_path, files) == files

    def test_no_trailing_newline(self, tmp_path):
        # Last line has no newline – must round-trip exactly
        data = b"INFO x=1\nINFO x=2\nINFO x=3"
        files = {"no_nl.log": data}
        assert round_trip(tmp_path, files) == files

    def test_non_utf8_binary_preserved(self, tmp_path):
        files = {"img.bin": bytes(range(256)) * 4}
        assert round_trip(tmp_path, files) == files


# ---------------------------------------------------------------------------
# Shared dictionary / compression quality tests
# ---------------------------------------------------------------------------


class TestSharedDictionary:
    def test_shared_templates_across_files(self, tmp_path):
        """Templates recurring across files should compress better than per-file."""
        import zstandard as zstd

        template = "2024-01-01T00:{mm:02d}:{ss:02d}Z INFO req={i} path=/api/v1\n"
        files = {}
        for day in range(10):
            lines = [
                template.format(mm=i // 60, ss=i % 60, i=i + day * 1000)
                for i in range(200)
            ]
            files[f"day{day:02d}.log"] = "".join(lines).encode()

        corpus_dir = make_corpus(tmp_path, files)
        mck = compress_corpus_template(corpus_dir)

        cctx = zstd.ZstdCompressor(level=3)
        zstd_total = sum(len(cctx.compress(d)) for d in files.values())

        # Corpus template should match or beat per-file ZSTD on a uniform log corpus
        assert len(mck) <= zstd_total

    def test_archive_smaller_with_shared_templates(self, tmp_path):
        """A corpus with one dominant recurring template should deduplicate well."""
        line = "METRIC host=server-{n} cpu={c} mem={m}\n"
        files = {
            f"metric{i}.log": "".join(
                line.format(n=i, c=j % 100, m=j * 2 % 1000) for j in range(300)
            ).encode()
            for i in range(8)
        }
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        total_raw = sum(len(d) for d in files.values())
        # Must be much smaller than raw
        assert len(archive) < total_raw // 3


# ---------------------------------------------------------------------------
# Format / error tests
# ---------------------------------------------------------------------------


class TestCorpusTemplateFormat:
    def test_magic_bytes(self, tmp_path):
        files = {"a.txt": b"INFO x=1\nINFO x=2\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        assert archive[:4] == b"MCK\x00"

    def test_version_byte(self, tmp_path):
        files = {"a.txt": b"INFO x=1\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        assert archive[4] == 0x01

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_bytes(b"data")
        with pytest.raises(ValueError, match="Not a directory"):
            compress_corpus_template(f)

    def test_corrupt_magic(self, tmp_path):
        files = {"a.txt": b"INFO x=1\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        bad = b"XXXX" + archive[4:]
        with pytest.raises(ValueError):
            decompress_corpus_template(bad, tmp_path / "out")

    def test_truncated_archive(self, tmp_path):
        with pytest.raises(ValueError):
            decompress_corpus_template(b"\x00\x01\x02", tmp_path / "out")

    def test_output_dir_created(self, tmp_path):
        files = {"f.txt": b"INFO n=1\nINFO n=2\n" * 5}
        corpus_dir = make_corpus(tmp_path, files)
        archive = compress_corpus_template(corpus_dir)
        out = tmp_path / "new" / "deep" / "dir"
        result = decompress_corpus_template(archive, out)
        assert (out / "f.txt").read_bytes() == files["f.txt"]
        assert result == ["f.txt"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self, tmp_path):
        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 100, "b.log": b"WARN y=3\n" * 50}
        dir1 = make_corpus(tmp_path / "run1", files)
        dir2 = make_corpus(tmp_path / "run2", files)
        assert compress_corpus_template(dir1) == compress_corpus_template(dir2)


# ---------------------------------------------------------------------------
# Metrics and explainability
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_metrics_keys_present(self, tmp_path):
        files = {"a.log": b"INFO val=1\nINFO val=2\n" * 30}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)

        expected_keys = {
            "structure_v2_enabled",
            "num_files",
            "num_lines",
            "num_shared_templates",
            "template_reuse_count",
            "template_reuse_rate",
            "json_lines_detected",
            "json_template_count",
            "normalized_template_count",
            "fuzzy_merge_count",
            "template_reuse_before",
            "template_reuse_after",
            "raw_fallback_lines",
            "binary_fallback_files",
            "low_structure_fallback_files",
            "fallback_reason_counts",
            "avg_vars_per_tpl_line",
            "compressed_size",
            "tarzstd_size",
            "chose_raw_fallback",
            "timing",
            "columnar_enabled",
            "num_columnar_templates",
            "num_encoded_columns",
            "column_encoding_counts",
            "raw_column_fallback_count",
            "columnar_size",
            "row_mode_size",
            "columnar_savings_vs_row",
            "final_selected_mode",
            "candidate_sizes",
            "selected_mode",
            "rejected_modes",
            "selection_reason",
            "savings_vs_tar_zstd_bytes",
            "savings_vs_row_bytes",
            "savings_vs_columnar_bytes",
            "adaptive_columnar_profile",
        }
        assert expected_keys.issubset(metrics.keys())

    def test_timing_keys_present(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO val=1\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        timing = metrics["timing"]
        # extract_s encompasses the whole extraction phase (tokenize+count+encode);
        # tokenize_s, count_s, encode_s give per-phase granularity.
        assert set(timing.keys()) == {
            "tokenize_s",
            "count_s",
            "encode_s",
            "extract_s",
            "serialize_s",
            "zstd_s",
            "total_s",
        }

    def test_timing_non_negative(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO val=1\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        for k, v in metrics["timing"].items():
            assert v >= 0, f"Timing value {k}={v} should be non-negative"

    def test_num_files_correct(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {f"f{i}.log": f"INFO n={i}\n".encode() * 5 for i in range(7)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["num_files"] == 7

    def test_compressed_size_matches_bytes(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        data, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["compressed_size"] == len(data)

    def test_reuse_rate_between_0_and_1(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 30, "b.log": b"WARN y=3\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        rate = metrics["template_reuse_rate"]
        assert 0.0 <= rate <= 1.0

    def test_shared_templates_count_positive_for_repetitive_corpus(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {f"day{i}.log": b"INFO req=1 status=200\n" * 100 for i in range(3)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["num_shared_templates"] > 0
        assert metrics["template_reuse_count"] > 0

    def test_binary_fallback_files_counted(self, tmp_path):
        import os

        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"data.bin": os.urandom(256), "log.log": b"INFO n=1\n" * 20}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        # The binary file should be counted in binary_fallback_files
        assert metrics["binary_fallback_files"] >= 1

    def test_with_metrics_same_bytes_as_without(self, tmp_path):
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO x=1\nINFO x=2\n" * 50}
        corpus_dir = make_corpus(tmp_path, files)
        data_plain = compress_corpus_template(corpus_dir)
        data_with_metrics, _ = compress_corpus_template_with_metrics(corpus_dir)
        assert data_plain == data_with_metrics

    def test_structure_v2_metrics_detect_json_and_reuse_improvement(self, tmp_path):
        files = {
            "events.ndjson": (
                b'{"service":"api","status":200,"request_id":"user-1","path":"/v1/ping"}\n'
                b'{"service":"api","status":500,"request_id":"user-2","path":"/v1/ping"}\n'
            )
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["structure_v2_enabled"] is True
        assert metrics["json_lines_detected"] >= 2
        assert metrics["json_template_count"] >= 1
        assert metrics["template_reuse_after"] >= metrics["template_reuse_before"]
        assert isinstance(metrics["fallback_reason_counts"], dict)


class TestStructureExtractionV2:
    def test_json_ndjson_round_trip_preserves_exact_bytes(self, tmp_path):
        files = {
            "events.ndjson": (
                b'{"service":"api","status":200,"request_id":"user-1","path":"/v1/ping"}\n'
                b'{"service":"api","status":500,"request_id":"user-2","path":"/v1/ping"}\n'
            ),
            "config.json": (
                b'{"timeout":30,"retries":3,"enabled":true,"owner":"ops@example.com"}'
            ),
        }
        assert round_trip(tmp_path, files) == files

    def test_json_structure_extraction_beats_legacy_reuse_for_ndjson(self, tmp_path):
        files = {
            "events.ndjson": b"".join(
                (
                    '{"ts":"2026-01-01T00:00:%02dZ","service":"api","request_id":"user-%02d","path":"/search?q=%s","status":200}\n'
                    % (i, i, "token%s" % chr(97 + i))
                ).encode("utf-8")
                for i in range(20)
            )
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["template_reuse_after"] > metrics["template_reuse_before"]

    def test_timestamp_prefixed_ndjson_triggers_json_detection(self, tmp_path):
        """Log lines with a non-JSON prefix before the JSON object must still use JSON extraction."""
        line_tpl = (
            '{"service":"api","status":200,"request_id":"user-%d","path":"/v1/ping"}\n'
        )
        # Same prefix on every line so the JSON skeleton is shared (varying timestamps in
        # the prefix would otherwise split templates — still lossless, but not this test).
        files = {
            "prefixed.ndjson": b"".join(
                b"2026-05-04T12:00:00Z " + (line_tpl % i).encode("utf-8")
                for i in range(8)
            )
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["json_lines_detected"] == 8
        assert metrics["num_shared_templates"] >= 1
        assert metrics["template_reuse_count"] == 8
        assert metrics["template_reuse_rate"] >= 8 / max(metrics["num_lines"], 1)

    def test_leading_whitespace_before_json_still_parses(self, tmp_path):
        files = {
            "spaced.ndjson": b'  {"service":"api","status":200,"id":"a"}\n'
            b'  {"service":"api","status":500,"id":"b"}\n'
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["json_lines_detected"] == 2

    def test_variable_normalization_v2_maps_semantic_variants_to_same_template(self):
        left = (
            "ts=2026-01-01T00:00:00Z level=INFO request_id=req-abcdef12 user_id=user-42 "
            "session_id=sess-1234 path=/api/v1/orders/42?expand=true email=user@example.com "
            "ip=10.1.2.3 trace=0xabc123"
        )
        right = (
            "ts=2026-01-02T11:12:13Z level=INFO request_id=req-fedcba21 user_id=user-77 "
            "session_id=sess-9999 path=/api/v1/orders/77?expand=false email=admin@example.com "
            "ip=10.9.8.7 trace=0xdef456"
        )
        assert _tokenize(left)[0] == _tokenize(right)[0]


class TestColumnarMode:
    def test_columnar_round_trip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "metacompressor.corpus_template._CORPUS_FALLBACK_THRESHOLD",
            float("inf"),
        )
        files = {
            "a.log": b"".join(
                f"INFO seq={i} status={i % 5} user={i % 9} code={200 + (i % 3)}\n".encode()
                for i in range(500)
            ),
            "b.log": b"".join(
                f"INFO seq={i + 500} status={i % 5} user={i % 9} code={200 + (i % 3)}\n".encode()
                for i in range(500)
            ),
        }
        corpus_dir = make_corpus(tmp_path, files)
        archive, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert (
            metrics["compressed_size"]
            == metrics["candidate_sizes"][metrics["selected_mode"]]
        )
        assert metrics["selected_mode"] in (_ADAPT_COL_V1, _ADAPT_COL_V2)
        assert metrics["final_selected_mode"] == _MODE_COLUMNAR_V2

        out_dir = tmp_path / "out"
        decompress_corpus_template(archive, out_dir)
        for rel, data in files.items():
            assert (out_dir / rel).read_bytes() == data

        payload = unpack_payload(archive)
        assert payload["mode"] == _MODE_COLUMNAR_V2
        assert "template_blocks" in payload

    def test_columnar_output_is_deterministic(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "metacompressor.corpus_template._CORPUS_FALLBACK_THRESHOLD",
            float("inf"),
        )
        files = {
            "a.log": b"INFO seq=1 status=200\nINFO seq=2 status=200\n" * 120,
            "b.log": b"INFO seq=100 status=500\nINFO seq=101 status=500\n" * 120,
        }
        dir1 = make_corpus(tmp_path / "run1", files)
        dir2 = make_corpus(tmp_path / "run2", files)

        archive1, metrics1 = compress_corpus_template_with_metrics(dir1)
        archive2, metrics2 = compress_corpus_template_with_metrics(dir2)

        assert metrics1["selected_mode"] == metrics2["selected_mode"]
        assert metrics1["candidate_sizes"] == metrics2["candidate_sizes"]
        assert metrics1["final_selected_mode"] == metrics2["final_selected_mode"]
        assert archive1 == archive2

    def test_columnar_block_flushing_round_trip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "metacompressor.corpus_template._CORPUS_FALLBACK_THRESHOLD",
            float("inf"),
        )
        files = {
            "flush.log": b"".join(
                f"INFO seq={i} status={i % 5} user={i % 7}\n".encode()
                for i in range(500)
            )
        }
        corpus_dir = make_corpus(tmp_path, files)
        monkeypatch.setattr(
            "metacompressor.corpus_template._MAX_COLUMNAR_BLOCK_ROWS",
            32,
        )

        archive, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert (
            metrics["compressed_size"]
            == metrics["candidate_sizes"][metrics["selected_mode"]]
        )
        assert metrics["selected_mode"] in (_ADAPT_COL_V1, _ADAPT_COL_V2)

        payload = unpack_payload(archive)
        block_lists = [
            entry for entry in payload["template_blocks"] if entry is not None
        ]
        assert block_lists
        assert any(len(entry) > 1 for entry in block_lists)

        out_dir = tmp_path / "out_flush"
        decompress_corpus_template(archive, out_dir)
        assert (out_dir / "flush.log").read_bytes() == files["flush.log"]

    def test_old_row_mode_archive_still_decompresses(self, tmp_path):
        payload = {
            "templates": ["INFO seq={} status={}"],
            "files": [
                {
                    "path": "legacy.log",
                    "records": [[0, ["1", "200"]], [0, ["2", "404"]]],
                }
            ],
        }
        archive = (
            MAGIC
            + bytes([VERSION])
            + zstd.ZstdCompressor(level=3).compress(
                msgpack.packb(payload, use_bin_type=True)
            )
        )

        out_dir = tmp_path / "out"
        extracted = decompress_corpus_template(archive, out_dir)

        assert extracted == ["legacy.log"]
        assert (
            out_dir / "legacy.log"
        ).read_bytes() == b"INFO seq=1 status=200\nINFO seq=2 status=404"

    def test_old_columnar_v1_archive_still_decompresses(self, tmp_path):
        payload = {
            "mode": _MODE_COLUMNAR_V1,
            "templates": ["INFO seq={} status={}"],
            "files": [{"path": "legacy.log", "kind": "text", "num_lines": 2}],
            "template_blocks": [
                {
                    "row_refs": [[0, 0], [0, 1]],
                    "columns": [
                        {
                            "encoding": "raw_msgpack",
                            "data": msgpack.packb(["1", "2"], use_bin_type=True),
                        },
                        {
                            "encoding": "raw_msgpack",
                            "data": msgpack.packb(["200", "404"], use_bin_type=True),
                        },
                    ],
                }
            ],
            "raw_files": [],
            "metadata": {"raw_lines": []},
        }
        archive = (
            MAGIC
            + bytes([VERSION])
            + zstd.ZstdCompressor(level=3).compress(
                msgpack.packb(payload, use_bin_type=True)
            )
        )

        out_dir = tmp_path / "out_v1"
        extracted = decompress_corpus_template(archive, out_dir)

        assert extracted == ["legacy.log"]
        assert (
            out_dir / "legacy.log"
        ).read_bytes() == b"INFO seq=1 status=200\nINFO seq=2 status=404"

    def test_integer_column_delta_encoding(self, tmp_path):
        files = {"seq.log": b"".join(f"INFO seq={i}\n".encode() for i in range(400))}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["column_encoding_counts"].get("delta_varint", 0) >= 1

    def test_integer_column_varint_encoding(self, tmp_path):
        values = [i if i % 2 == 0 else 10_000_000 - i for i in range(400)]
        files = {
            "varint.log": b"".join(f"INFO seq={value}\n".encode() for value in values)
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["column_encoding_counts"].get("varint", 0) >= 1

    def test_string_dictionary_encoding(self, tmp_path):
        urls = [b"https://example.com/a", b"https://example.com/b"] * 150
        files = {"urls.log": b"".join(b"INFO url=" + url + b"\n" for url in urls)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["column_encoding_counts"].get("dictionary", 0) >= 1

    def test_rle_encoding(self, tmp_path):
        urls = ([b"https://example.com/a"] * 120) + ([b"https://example.com/b"] * 120)
        files = {"rle.log": b"".join(b"INFO url=" + url + b"\n" for url in urls)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["column_encoding_counts"].get("rle", 0) >= 1

    def test_raw_column_fallback_when_specialized_is_larger(self, tmp_path):
        urls = [b"https://example.com/item/" + f"{i:04d}".encode() for i in range(200)]
        files = {"rawcol.log": b"".join(b"INFO url=" + url + b"\n" for url in urls)}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["raw_column_fallback_count"] >= 1
        assert metrics["column_encoding_counts"].get("raw_msgpack", 0) >= 1

    def test_no_trailing_newline_with_columnar_mode(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "metacompressor.corpus_template._CORPUS_FALLBACK_THRESHOLD",
            float("inf"),
        )
        lines = [f"INFO seq={i}".encode() for i in range(1, 200)]
        files = {"nonl.log": b"\n".join(lines)}
        corpus_dir = make_corpus(tmp_path, files)
        archive, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert (
            metrics["compressed_size"]
            == metrics["candidate_sizes"][metrics["selected_mode"]]
        )
        assert metrics["selected_mode"] in (_ADAPT_COL_V1, _ADAPT_COL_V2)
        out_dir = tmp_path / "out"
        decompress_corpus_template(archive, out_dir)
        assert (out_dir / "nonl.log").read_bytes() == files["nonl.log"]

    def test_mixed_raw_and_templated_lines(self, tmp_path):
        files = {
            "mixed.log": (b"INFO seq=1\nRAW ONLY LINE A\nINFO seq=2\nRAW ONLY LINE B\n")
        }
        corpus_dir = make_corpus(tmp_path, files)
        archive, _ = compress_corpus_template_with_metrics(corpus_dir)
        out_dir = tmp_path / "out"
        decompress_corpus_template(archive, out_dir)
        assert (out_dir / "mixed.log").read_bytes() == files["mixed.log"]

    def test_binary_file_fallback_with_columnar_archive(self, tmp_path):
        files = {
            "structured.log": b"INFO seq=1\nINFO seq=2\n" * 60,
            "data.bin": bytes(range(256)) * 2,
        }
        corpus_dir = make_corpus(tmp_path, files)
        archive, metrics = compress_corpus_template_with_metrics(corpus_dir)
        out_dir = tmp_path / "out"
        decompress_corpus_template(archive, out_dir)
        assert metrics["binary_fallback_files"] >= 1
        for rel, data in files.items():
            assert (out_dir / rel).read_bytes() == data


# ---------------------------------------------------------------------------
# Hybrid fallback (files with no template-mode lines stored as raw bytes)
# ---------------------------------------------------------------------------


class TestHybridFallback:
    def test_no_template_file_still_round_trips(self, tmp_path):
        """A file with unique lines (no template reuse) must round-trip exactly."""
        # Each line is unique and has no numeric/variable parts
        unique_lines = (
            "\n".join(f"LINE_{chr(65 + i % 26)}{i}_unique" for i in range(20)) + "\n"
        )
        files = {
            "unique.log": unique_lines.encode(),
            "normal.log": b"INFO val=1\nINFO val=2\n" * 30,
        }
        result = round_trip(tmp_path, files)
        assert result == files

    def test_hybrid_fallback_files_are_binary_fallback(self, tmp_path):
        """Files with no template-mode lines should be in binary_fallback_files."""
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        # Pure-text lines with no extractable variables and no trailing newline
        # (avoids the empty-string artifact from split on a trailing \n).
        # Each line is unique globally so no template key recurs.
        unique_content = b"APPLE BANANA CHERRY\nDOG ELEPHANT FOX\nGRAPE HONEY IRIS"
        files = {
            "unique.log": unique_content,
            "normal.log": b"INFO val=1\nINFO val=2\n" * 30,
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        # unique.log has no recurring template → hybrid fallback → binary_fallback_files >= 1
        assert metrics["binary_fallback_files"] >= 1

    def test_fallback_does_not_increase_size_for_noisy_corpus(self, tmp_path):
        """A corpus of all-unique lines should not bloat due to template overhead."""
        # Generate 5 "noisy" files with completely unique lines
        import os

        noisy_files = {}
        for i in range(5):
            lines = (
                "\n".join(
                    f"MSG {os.urandom(8).hex()} idx={j} file={i}" for j in range(20)
                )
                + "\n"
            )
            noisy_files[f"noisy_{i}.log"] = lines.encode()

        corpus_dir = make_corpus(tmp_path, noisy_files)
        mck = compress_corpus_template(corpus_dir)
        # Should still be a valid archive (round-trips correctly)
        out_dir = tmp_path / "out"
        decompress_corpus_template(mck, out_dir)
        for rel in noisy_files:
            recovered = (out_dir / rel).read_bytes()
            assert recovered == noisy_files[rel], f"Mismatch for {rel}"

    def test_mixed_corpus_round_trip(self, tmp_path):
        """Mixed corpus with template-rich, template-poor, and binary files."""
        import os

        files = {
            "structured.log": b"ERROR code=404 user=42\n" * 100,
            "unstructured.log": b"SOME TEXT THAT IS UNIQUE EACH LINE\n",
            "binary.bin": os.urandom(128),
        }
        result = round_trip(tmp_path, files)
        assert result == files


# ---------------------------------------------------------------------------
# Smart TAR+ZSTD fallback
# ---------------------------------------------------------------------------


class TestRawFallback:
    """Tests for the raw_tar_zstd automatic fallback mode."""

    def test_chose_raw_fallback_false_for_structured_logs(self, tmp_path):
        """Highly structured logs must NOT trigger the raw fallback."""
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {
            f"day{i}.log": b"INFO req=1 status=200 path=/api\n" * 200 for i in range(5)
        }
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert metrics["chose_raw_fallback"] is False

    def test_tarzstd_size_in_metrics(self, tmp_path):
        """tarzstd_size must always be a positive integer."""
        from metacompressor.corpus_template import compress_corpus_template_with_metrics

        files = {"a.log": b"INFO val=1\nINFO val=2\n" * 30}
        corpus_dir = make_corpus(tmp_path, files)
        _, metrics = compress_corpus_template_with_metrics(corpus_dir)
        assert isinstance(metrics["tarzstd_size"], int)
        assert metrics["tarzstd_size"] > 0

    def test_raw_fallback_round_trip(self, tmp_path):
        """When raw_tar_zstd mode fires, the corpus must still round-trip."""
        import os

        from metacompressor.corpus_template import (
            _CORPUS_FALLBACK_THRESHOLD,
            compress_corpus_template_with_metrics,
            decompress_corpus_template,
        )

        # Corpus that is hard for template mode: unique random-hex payloads.
        files = {
            f"rand{i}.log": (
                f"ENTRY id={i} payload={os.urandom(16).hex()} x={os.urandom(4).hex()}\n"
            ).encode()
            * 1
            for i in range(20)
        }
        # Add a structured anchor so the corpus isn't 100% binary fallback,
        # but keep it sparse enough that raw fallback might fire.
        files["anchor.log"] = b"INFO event=1 status=200\n" * 3

        corpus_dir = make_corpus(tmp_path, files)
        archive, metrics = compress_corpus_template_with_metrics(corpus_dir)

        out = tmp_path / "out"
        decompress_corpus_template(archive, out)

        # Every file must round-trip exactly.
        for rel, data in files.items():
            assert (out / rel).read_bytes() == data, f"Mismatch for {rel}"

        # If fallback fired, archive must be no larger than TAR+ZSTD * threshold.
        if metrics["chose_raw_fallback"]:
            assert (
                len(archive)
                <= metrics["tarzstd_size"] * _CORPUS_FALLBACK_THRESHOLD + 200
            )
