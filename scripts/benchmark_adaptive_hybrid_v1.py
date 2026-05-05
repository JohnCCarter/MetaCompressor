#!/usr/bin/env python3
"""Benchmark adaptive v1 vs v2.2 vs v2.2+hybrid (hybrid_row_columnar_v1 pool)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from metacompressor.corpus_template import compress_corpus_template_with_metrics

ADAPTIVE_MODES = ("v1", "v2.2", "v2.2+hybrid")


def _write(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _bench(name: str, files: Dict[str, bytes]) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_bench_hybrid_"))
    _write(root, files)
    row: Dict[str, object] = {"dataset": name}
    for mode in ADAPTIVE_MODES:
        _, metrics = compress_corpus_template_with_metrics(root, adaptive=mode)
        tar = int(metrics["tarzstd_size"])
        size = int(metrics["compressed_size"])
        delta_pct = 0.0 if tar == 0 else 100.0 * (size - tar) / tar
        pv2 = metrics.get("predictive_v2") or {}
        hy = metrics.get("hybrid_row_columnar_v1")
        row[mode] = {
            "size": size,
            "tar": tar,
            "wins_tar": size < tar,
            "delta_pct": delta_pct,
            "build_time_s": float(metrics["timing"]["total_s"]),
            "encode_s": float(metrics["timing"]["encode_s"]),
            "selected": metrics["selected_mode"],
            "skipped_builds": bool(pv2.get("skipped_template_builds")),
            "prediction_error": pv2.get("error"),
            "confidence_band": pv2.get("confidence_band", ""),
            "score_gap": pv2.get("score_gap"),
            "aggression_factor": pv2.get("aggression_factor"),
            "prediction_confidence": pv2.get("prediction_confidence"),
            "model_quality": pv2.get("model_quality"),
            "structure_score": pv2.get("structure_score"),
            "structure_signal_strong": pv2.get("structure_signal_strong"),
            "hybrid_eligible": (hy or {}).get("eligible") if hy else None,
            "hybrid_overhead_vs_col": (
                (hy or {}).get("estimated_overhead_vs_columnar_v2_bytes")
                if hy
                else None
            ),
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
            f"INFO user={i % 9} item={i} status={200 + (i % 3)}\n".encode()
            for i in range(180)
        )
    }
    return [
        ("unique lines n=35", unique_small),
        ("structured repeat n=600", structured),
        ("high-cardinality ids n=300", high_cardinality),
        ("many-small-files n=80", many_small),
        ("mixed fields n=180", mixed),
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
    out = repo / "results" / "metacompressor_adaptive_hybrid_v1_benchmark.md"
    lines = [
        "# Adaptive v1 vs v2.2 vs v2.2+hybrid benchmark",
        "",
        'Each dataset is compressed with `adaptive="v1"`, `adaptive="v2.2"`, and '
        '`adaptive="v2.2+hybrid"`. The hybrid mode adds **hybrid_row_columnar_v1** '
        "(per-block dense row table vs columnar encoding) to the v2.2 predictive pool "
        "with tie-break preference over pure columnar v2 when final `.mck` sizes tie.",
        "",
        "Delta is `(compressed_size - tarzstd_size) / tarzstd_size`; negative means "
        "smaller than plain TAR+ZSTD.",
        "",
        "## Summary",
        "",
        "| Mode | Win-rate vs TAR+ZSTD | Avg delta | Worst loss | Avg build time | Avg encode time |",
        "| ---- | -------------------: | --------: | ---------: | -------------: | --------------: |",
    ]
    for mode in ADAPTIVE_MODES:
        s = summary[mode]
        lines.append(
            f"| `{mode}` | {s['win_rate']:.1f}% | {s['avg_delta_pct']:.2f}% | "
            f"{s['worst_loss_pct']:.2f}% | {s['avg_build_time_s']:.4f}s | "
            f"{s['avg_encode_s']:.4f}s |"
        )
    lines.extend(
        [
            "",
            "## Per dataset",
            "",
            "| Dataset | Mode | Selected | Delta vs TAR | Hybrid eligible | Overhead vs col |",
            "| ------- | ---- | -------- | -----------: | :--------------- | --------------: |",
        ]
    )
    for row in rows:
        for mode in ADAPTIVE_MODES:
            r = row[mode]
            assert isinstance(r, dict)
            oh = r["hybrid_overhead_vs_col"]
            oh_s = "" if oh is None else str(int(oh))
            hel = r["hybrid_eligible"]
            hel_s = "" if hel is None else str(hel)
            lines.append(
                f"| {row['dataset']} | `{mode}` | `{r['selected']}` | "
                f"{float(r['delta_pct']):.2f}% | {hel_s} | {oh_s} |"
            )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `v2.2+hybrid` uses the same predictor as `v2.2` and adds one extra encode pass "
            "for hybrid_row_columnar_v1 when columnar v2 is built.",
            "- Pool order tie-break among equal-size candidates: row < hybrid < columnar v2 < TAR.",
            "",
        ]
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    for mode in ADAPTIVE_MODES:
        s = summary[mode]
        print(
            f"{mode}: avg_delta={s['avg_delta_pct']:.2f}% worst_loss={s['worst_loss_pct']:.2f}%"
        )


if __name__ == "__main__":
    main()
