#!/usr/bin/env python3
"""Benchmark v2.2 (baseline) vs v2.3 predictive-only with profiles."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from metacompressor.corpus_template import compress_corpus_template_with_metrics


def _write(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _bench(name: str, files: Dict[str, bytes], profile: str) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_v23_bench_"))
    _write(root, files)
    _, m22 = compress_corpus_template_with_metrics(
        root, adaptive="v2.2", profile="generic"
    )
    _, m23 = compress_corpus_template_with_metrics(
        root, adaptive="v2.3", profile=profile
    )
    return {
        "dataset": name,
        "profile": profile,
        "v22_size": int(m22["compressed_size"]),
        "v22_delta": 100.0
        * (int(m22["compressed_size"]) - int(m22["tarzstd_size"]))
        / max(1, int(m22["tarzstd_size"])),
        "v22_time": float(m22["timing"]["total_s"]),
        "v22_mode": m22["selected_mode"],
        "v23_size": int(m23["compressed_size"]),
        "v23_delta": 100.0
        * (int(m23["compressed_size"]) - int(m23["tarzstd_size"]))
        / max(1, int(m23["tarzstd_size"])),
        "v23_time": float(m23["timing"]["total_s"]),
        "v23_mode": m23["selected_mode"],
        "v23_ranked": (
            ((m23.get("predictive_v2") or {}).get("v23") or {}).get("ranked_candidates")
            or []
        ),
    }


def _datasets() -> List[Tuple[str, Dict[str, bytes]]]:
    mixed = {
        "mix.log": b"".join(
            (
                f"INFO user={i % 9} path=https://api.example.com/v1/users/{i}/items.json "
                f"item={i} trace=/api/v1/events/{i % 12}.json status={200 + (i % 3)}\n"
            ).encode()
            for i in range(300)
        )
    }
    nginx_like = {
        "access.log": b"".join(
            (
                f"{(i % 250) + 1}.10.2.{i % 255} - - [01/May/2026:12:{i % 60:02d}:00 +0000] "
                f'"GET /api/v1/orders/{i % 30}/item.json HTTP/1.1" {200 + (i % 5)} 1234\n'
            ).encode()
            for i in range(500)
        )
    }
    return [("mixed logs n=300", mixed), ("nginx-like n=500", nginx_like)]


def main() -> None:
    rows: List[Dict[str, object]] = []
    for name, files in _datasets():
        rows.append(_bench(name, files, "logs"))
        rows.append(_bench(name, files, "nginx"))
    repo = Path(__file__).resolve().parents[1]
    out = repo / "results" / "metacompressor_v23_predictive_profiles_benchmark.md"
    v22_win = sum(1 for r in rows if float(r["v22_delta"]) < 0.0)
    v23_win = sum(1 for r in rows if float(r["v23_delta"]) < 0.0)
    n = max(1, len(rows))
    v22_avg = sum(float(r["v22_delta"]) for r in rows) / n
    v23_avg = sum(float(r["v23_delta"]) for r in rows) / n
    v22_worst = max(0.0, max(float(r["v22_delta"]) for r in rows))
    v23_worst = max(0.0, max(float(r["v23_delta"]) for r in rows))
    v22_time = sum(float(r["v22_time"]) for r in rows) / n
    v23_time = sum(float(r["v23_time"]) for r in rows) / n
    lines = [
        "# v2.2 vs v2.3 predictive+profiles benchmark",
        "",
        "v2.2 uses current baseline behavior. v2.3 uses predictive-only build (top-1/top-2) with profile-aware ranking.",
        "",
        "## Summary",
        "",
        "| Mode | Win rate | Avg delta | Worst loss | Avg time |",
        "| ---- | -------: | --------: | ---------: | -------: |",
        f"| `v2.2` | {100.0 * v22_win / n:.1f}% | {v22_avg:.2f}% | {v22_worst:.2f}% | {v22_time:.4f}s |",
        f"| `v2.3` | {100.0 * v23_win / n:.1f}% | {v23_avg:.2f}% | {v23_worst:.2f}% | {v23_time:.4f}s |",
        "",
        "## Per dataset",
        "",
        "| Dataset | Profile | v2.2 mode | v2.2 delta | v2.2 time | v2.3 mode | v2.3 delta | v2.3 time | Ranked (v2.3) |",
        "| ------- | ------- | --------- | ---------: | --------: | --------- | ---------: | --------: | ------------- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['dataset']} | `{r['profile']}` | `{r['v22_mode']}` | {float(r['v22_delta']):.2f}% | "
            f"{float(r['v22_time']):.4f}s | `{r['v23_mode']}` | {float(r['v23_delta']):.2f}% | "
            f"{float(r['v23_time']):.4f}s | `{list(r['v23_ranked'])}` |"
        )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
