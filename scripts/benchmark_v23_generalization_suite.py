#!/usr/bin/env python3
"""Broader v2.3 generalization validation benchmark suite."""

from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

from metacompressor.corpus_template import (
    compress_corpus_template_with_metrics,
    decompress_corpus_template,
)


def _write(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _noise_bytes(seed: int, n: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(0, 256) for _ in range(n))


def _dataset_specs() -> List[Tuple[str, str, Dict[str, bytes]]]:
    # name, profile, files
    specs: List[Tuple[str, str, Dict[str, bytes]]] = []

    specs.append(
        (
            "json_logs_small",
            "json",
            {
                "events.jsonl": b"".join(
                    (
                        '{"service":"auth","level":"info","status":200,'
                        f'"region":"{["iad","sfo","fra"][i % 3]}","route":"/token/{i % 11}","user":{i % 37}}}\n'
                    ).encode()
                    for i in range(220)
                )
            },
        )
    )
    specs.append(
        (
            "json_logs_large",
            "json",
            {
                "events.jsonl": b"".join(
                    (
                        '{"service":"checkout","level":"info","status":200,'
                        f'"region":"{["iad","sfo","fra","sin"][i % 4]}","route":"/orders/{i % 21}/items",'
                        f'"user":{i % 90},"latency":{20 + (i % 100)}}}\n'
                    ).encode()
                    for i in range(1600)
                )
            },
        )
    )
    specs.append(
        (
            "ndjson_app_logs_medium",
            "logs",
            {
                "app.ndjson": b"".join(
                    (
                        '{"ts":"2026-05-05T10:%02d:%02dZ","msg":"ok","status":%d,'
                        '"trace":"%08d","path":"/api/v1/orders/%d"}\n'
                        % (i // 60, i % 60, 200 + (i % 4), i, i % 30)
                    ).encode()
                    for i in range(900)
                )
            },
        )
    )
    specs.append(
        (
            "nginx_access_medium",
            "nginx",
            {
                "access.log": b"".join(
                    (
                        f"{(i % 250) + 1}.10.2.{i % 255} - - [05/May/2026:12:{i % 60:02d}:00 +0000] "
                        f'"GET /api/v1/orders/{i % 35}/item.json HTTP/1.1" {200 + (i % 5)} 1234\n'
                    ).encode()
                    for i in range(900)
                )
            },
        )
    )
    specs.append(
        (
            "timestamp_heavy_small",
            "logs",
            {
                "ts.log": b"".join(
                    (
                        f"INFO ts=2026-05-05T11:{i // 60:02d}:{i % 60:02d}Z "
                        f"service=worker task={i % 13} status={200 + (i % 3)}\n"
                    ).encode()
                    for i in range(260)
                )
            },
        )
    )
    specs.append(
        (
            "timestamp_heavy_large",
            "logs",
            {
                "ts.log": b"".join(
                    (
                        f"INFO ts=2026-05-05T12:{i // 60:02d}:{i % 60:02d}Z "
                        f"service=api route=/v2/x/{i % 19} status={200 + (i % 7)} trace={i:08d}\n"
                    ).encode()
                    for i in range(1800)
                )
            },
        )
    )
    specs.append(
        (
            "high_cardinality_ids_medium",
            "generic",
            {
                "ids.log": b"".join(
                    b"INFO trace=%032x status=%d\n" % (i * 7919 + 17, 200 + (i % 5))
                    for i in range(1100)
                )
            },
        )
    )
    specs.append(
        (
            "many_small_files_small",
            "generic",
            {f"shards/s{i:04d}.log": b"OK row=1\n" for i in range(180)},
        )
    )
    specs.append(
        (
            "many_small_files_large",
            "generic",
            {f"parts/p{i:05d}.log": f"line-{i}\n".encode() for i in range(900)},
        )
    )
    specs.append(
        (
            "random_noise_binary",
            "generic",
            {
                "noise/a.bin": _noise_bytes(7, 120_000),
                "noise/b.bin": _noise_bytes(8, 96_000),
                "noise/c.bin": _noise_bytes(9, 80_000),
            },
        )
    )
    specs.append(
        (
            "mixed_structured_logs_medium",
            "logs",
            {
                "mix.log": b"".join(
                    (
                        f"INFO user={i % 17} path=https://api.example.com/v1/users/{i}/items.json "
                        f"item={i} trace=/api/v1/events/{i % 20}.json status={200 + (i % 4)}\n"
                    ).encode()
                    for i in range(780)
                )
            },
        )
    )
    specs.append(
        (
            "semi_structured_messages",
            "generic",
            {
                "messages.log": b"".join(
                    (
                        f"[{i:05d}] worker-{i % 7} said='ok' extra=id:{i * 13};"
                        f"user={i % 41};route=/r/{i % 9};note=hello-{i % 5}\n"
                    ).encode()
                    for i in range(820)
                )
            },
        )
    )
    specs.append(
        (
            "small_corpus_mixed",
            "logs",
            {
                "a.log": b"INFO n=1 status=200\n" * 45,
                "b.log": b"INFO n=2 status=201\n" * 35,
            },
        )
    )
    specs.append(
        (
            "medium_corpus_mixed",
            "logs",
            {
                "app.log": b"".join(
                    (
                        f"INFO tenant={i % 9} service=api route=/x/{i % 12} "
                        f"status={200 + (i % 3)} dur_ms={20 + (i % 100)}\n"
                    ).encode()
                    for i in range(1200)
                )
            },
        )
    )
    specs.append(
        (
            "large_corpus_mixed",
            "logs",
            {
                "big.log": b"".join(
                    (
                        f"INFO tenant={i % 23} service=checkout route=/orders/{i % 70}/line/{i % 5} "
                        f"status={200 + (i % 5)} dur_ms={10 + (i % 240)}\n"
                    ).encode()
                    for i in range(4200)
                )
            },
        )
    )
    return specs


def _bench(name: str, profile: str, files: Dict[str, bytes]) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_v23_gen_"))
    _write(root, files)
    t0 = time.perf_counter()
    archive, metrics = compress_corpus_template_with_metrics(
        root, adaptive="v2.3", profile=profile
    )
    encode_time = time.perf_counter() - t0
    out = Path(tempfile.mkdtemp(prefix="mc_v23_gen_out_"))
    t1 = time.perf_counter()
    decompress_corpus_template(archive, out)
    decode_time = time.perf_counter() - t1

    tar = int(metrics["tarzstd_size"])
    size = int(metrics["compressed_size"])
    delta_pct = 0.0 if tar == 0 else 100.0 * (size - tar) / tar
    v23 = (metrics.get("predictive_v2") or {}).get("v23") or {}
    return {
        "dataset": name,
        "profile": profile,
        "selected_mode": metrics["selected_mode"],
        "wins_tar": size < tar,
        "delta_pct": delta_pct,
        "worst_loss_pct": max(0.0, delta_pct),
        "candidate_count": int(v23.get("built_candidate_count", 0)),
        "encode_time_s": encode_time,
        "decode_time_s": decode_time,
        "size": size,
        "tar": tar,
        "fallback_triggered": bool(metrics.get("fallback_triggered", False)),
        "fallback_reason": metrics.get("fallback_reason"),
    }


def main() -> None:
    rows = [_bench(name, profile, files) for name, profile, files in _dataset_specs()]
    n = max(1, len(rows))
    win_rate = 100.0 * sum(1 for r in rows if bool(r["wins_tar"])) / n
    avg_delta = sum(float(r["delta_pct"]) for r in rows) / n
    worst_loss = max(float(r["worst_loss_pct"]) for r in rows)
    avg_candidates = sum(int(r["candidate_count"]) for r in rows) / n
    avg_encode = sum(float(r["encode_time_s"]) for r in rows) / n
    avg_decode = sum(float(r["decode_time_s"]) for r in rows) / n
    fallback_count = sum(1 for r in rows if bool(r["fallback_triggered"]))
    fallback_reasons: Dict[str, int] = {}
    for r in rows:
        reason = r.get("fallback_reason")
        if reason:
            fallback_reasons[str(reason)] = fallback_reasons.get(str(reason), 0) + 1

    repo = Path(__file__).resolve().parents[1]
    out = repo / "results" / "metacompressor_v23_generalization_suite.md"
    lines = [
        "# v2.3 generalization validation benchmark suite",
        "",
        "Broader validation across heterogeneous corpora to measure generalization (not weight tuning).",
        "",
        "## Summary",
        "",
        f"- Dataset count: **{n}**",
        f"- Win-rate vs TAR+ZSTD: **{win_rate:.1f}%**",
        f"- Avg delta: **{avg_delta:.2f}%**",
        f"- Worst loss: **{worst_loss:.2f}%**",
        f"- Avg candidates built: **{avg_candidates:.2f}**",
        f"- Avg encode time: **{avg_encode:.4f}s**",
        f"- Avg decode time: **{avg_decode:.4f}s**",
        f"- Fallback triggered count: **{fallback_count}**",
        f"- Fallback reasons: **{fallback_reasons}**",
        "",
        "## Per dataset",
        "",
        "| Dataset | Profile | Selected mode | Win vs TAR | Delta | Candidate count | Fallback triggered | Fallback reason | Encode time | Decode time |",
        "| ------- | ------- | ------------- | ---------: | ----: | --------------: | -----------------: | -------------- | ----------: | ----------: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['dataset']} | `{row['profile']}` | `{row['selected_mode']}` | "
            f"{'Y' if bool(row['wins_tar']) else 'N'} | {float(row['delta_pct']):.2f}% | "
            f"{int(row['candidate_count'])} | "
            f"{'Y' if bool(row['fallback_triggered']) else 'N'} | "
            f"`{row['fallback_reason']}` | "
            f"{float(row['encode_time_s']):.4f}s | "
            f"{float(row['decode_time_s']):.4f}s |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
