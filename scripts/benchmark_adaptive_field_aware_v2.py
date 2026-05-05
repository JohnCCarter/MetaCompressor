#!/usr/bin/env python3
"""Benchmark adaptive v2.2 vs field-aware vs string-pattern vs pipeline vs relational."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from metacompressor.corpus_template import compress_corpus_template_with_metrics

ADAPTIVE_MODES = (
    "v2.2",
    "v2.2+field_aware",
    "v2.2+string_pattern",
    "v2.2+pipeline",
    "v2.2+relational",
)


def _write(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _bench(name: str, files: Dict[str, bytes]) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_bench_fa_"))
    _write(root, files)
    row: Dict[str, object] = {"dataset": name}
    for mode in ADAPTIVE_MODES:
        _, metrics = compress_corpus_template_with_metrics(root, adaptive=mode)
        tar = int(metrics["tarzstd_size"])
        size = int(metrics["compressed_size"])
        delta_pct = 0.0 if tar == 0 else 100.0 * (size - tar) / tar
        pv2 = metrics.get("predictive_v2") or {}
        row[mode] = {
            "size": size,
            "tar": tar,
            "wins_tar": size < tar,
            "delta_pct": delta_pct,
            "build_time_s": float(metrics["timing"]["total_s"]),
            "encode_s": float(metrics["timing"]["encode_s"]),
            "selected": metrics["selected_mode"],
            "skipped_builds": bool(pv2.get("skipped_template_builds")),
        }
    return row


def _datasets() -> List[Tuple[str, Dict[str, bytes]]]:
    unique_small = {f"u{i}.log": f"line-{i}\n".encode() for i in range(35)}
    structured = {"app.log": b"INFO seq=1 status=200 path=/ok\n" * 600}
    high_cardinality = {
        "ids.log": b"".join(
            b"INFO trace=%032x status=%d\n" % (i * 7919 + 17, 200 + (i % 5))
            for i in range(300)
        )
    }
    many_small = {f"shard/{i:04d}.log": b"OK row=1\n" for i in range(80)}
    mixed = {
        "mix.log": b"".join(
            (
                f"INFO user={i % 9} path=https://api.example.com/v1/users/{i}/items.json "
                f"item={i} trace=/api/v1/events/{i % 12}.json "
                f"status={200 + (i % 3)}\n"
            ).encode()
            for i in range(300)
        )
    }
    ts_heavy = {
        "ts.log": b"".join(
            (f"INFO ts=2024-06-15 12:{i // 60:02d}:{i % 60:02d} ok=1\n".encode())
            for i in range(400)
        )
    }
    return [
        ("unique lines n=35", unique_small),
        ("structured repeat n=600", structured),
        ("high-cardinality ids n=300", high_cardinality),
        ("many-small-files n=80", many_small),
        ("mixed fields n=300", mixed),
        ("timestamp-heavy n=400", ts_heavy),
    ]


def _summary(rows: List[Dict[str, object]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for mode in ADAPTIVE_MODES:
        entries = [r[mode] for r in rows]
        mode_rows = [e for e in entries if isinstance(e, dict)]
        out[mode] = {
            "win_rate": 100.0
            * sum(1 for e in mode_rows if e["wins_tar"])
            / len(mode_rows),
            "avg_delta_pct": sum(float(e["delta_pct"]) for e in mode_rows)
            / len(mode_rows),
            "worst_loss_pct": max(0.0, max(float(e["delta_pct"]) for e in mode_rows)),
            "avg_build_time_s": sum(float(e["build_time_s"]) for e in mode_rows)
            / len(mode_rows),
            "avg_encode_s": sum(float(e["encode_s"]) for e in mode_rows)
            / len(mode_rows),
        }
    return out


def main() -> None:
    rows = [_bench(n, f) for n, f in _datasets()]
    summary = _summary(rows)
    repo = Path(__file__).resolve().parents[1]
    out = repo / "results" / "metacompressor_adaptive_field_aware_v2_benchmark.md"
    lines = [
        "# Adaptive v2.2 vs v2.2+field_aware vs v2.2+string_pattern vs v2.2+pipeline vs v2.2+relational benchmark",
        "",
        "Delta is `(compressed_size - tarzstd_size) / tarzstd_size`; negative is better.",
        "",
        "## Summary",
        "",
        "| Mode | Win-rate vs TAR | Avg delta | Worst loss | Avg build | Avg encode |",
        "| ---- | --------------: | --------: | ---------: | --------: | ---------: |",
    ]
    for mode in ADAPTIVE_MODES:
        s = summary[mode]
        lines.append(
            f"| `{mode}` | {s['win_rate']:.1f}% | {s['avg_delta_pct']:.2f}% | "
            f"{s['worst_loss_pct']:.2f}% | {s['avg_build_time_s']:.4f}s | "
            f"{s['avg_encode_s']:.4f}s |"
        )
    lines.extend(["", "## Per dataset", ""])
    for row in rows:
        lines.append(f"### {row['dataset']}")
        lines.append("")
        lines.append("| Mode | Selected | Delta % | Size |")
        lines.append("| ---- | -------- | ------: | ---: |")
        for mode in ADAPTIVE_MODES:
            r = row[mode]
            assert isinstance(r, dict)
            lines.append(
                f"| `{mode}` | `{r['selected']}` | {float(r['delta_pct']):.2f}% | "
                f"{r['size']} |"
            )
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    for mode in ADAPTIVE_MODES:
        s = summary[mode]
        print(
            f"{mode}: avg_delta={s['avg_delta_pct']:.2f}% "
            f"worst_loss={s['worst_loss_pct']:.2f}%"
        )


if __name__ == "__main__":
    main()
