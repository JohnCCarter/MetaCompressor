#!/usr/bin/env python3
"""Benchmark adaptive v1 vs v2/v2.1/v2.2 predictive selection."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from metacompressor.corpus_template import compress_corpus_template_with_metrics

ADAPTIVE_MODES = ("v1", "v2", "v2.1", "v2.2")


def _write(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _bench(name: str, files: Dict[str, bytes]) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_bench_"))
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
            "prediction_error": pv2.get("error"),
            "confidence_band": pv2.get("confidence_band", ""),
            "score_gap": pv2.get("score_gap"),
            "aggression_factor": pv2.get("aggression_factor"),
            "prediction_confidence": pv2.get("prediction_confidence"),
            "model_quality": pv2.get("model_quality"),
            "structure_score": pv2.get("structure_score"),
            "structure_signal_strong": pv2.get("structure_signal_strong"),
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


def _confidence_summary(rows: List[Dict[str, object]], mode: str) -> Dict[str, float]:
    mode_rows = [r[mode] for r in rows if isinstance(r[mode], dict)]
    high = [r for r in mode_rows if r["confidence_band"] == "high"]
    low = [r for r in mode_rows if r["confidence_band"] != "high"]

    def avg_delta(entries: List[Dict[str, object]]) -> float:
        if not entries:
            return 0.0
        return sum(float(e["delta_pct"]) for e in entries) / len(entries)

    def win_rate(entries: List[Dict[str, object]]) -> float:
        if not entries:
            return 0.0
        return 100.0 * sum(1 for e in entries if e["wins_tar"]) / len(entries)

    def fallback_rate(entries: List[Dict[str, object]]) -> float:
        if not entries:
            return 0.0
        return (
            100.0
            * sum(1 for e in entries if e["selected"] == "raw_tar_zstd")
            / len(entries)
        )

    def worst_loss(entries: List[Dict[str, object]]) -> float:
        if not entries:
            return 0.0
        return max(0.0, max(float(e["delta_pct"]) for e in entries))

    return {
        "high_count": float(len(high)),
        "high_avg_delta": avg_delta(high),
        "high_win_rate": win_rate(high),
        "low_count": float(len(low)),
        "low_fallback_rate": fallback_rate(low),
        "low_worst_loss": worst_loss(low),
    }


def main() -> None:
    rows = [_bench(n, f) for n, f in _datasets()]
    summary = _summary(rows)
    confidence_summaries = {
        mode: _confidence_summary(rows, mode) for mode in ("v2.1", "v2.2")
    }
    repo = Path(__file__).resolve().parents[1]
    out = repo / "results" / "metacompressor_adaptive_v2_2_predictive.md"
    lines = [
        "# Adaptive v1 vs v2/v2.1/v2.2 predictive benchmark",
        "",
        'Each dataset is compressed with `adaptive="v1"`, `adaptive="v2"`, '
        '`adaptive="v2.1"`, and `adaptive="v2.2"`. Delta is '
        "`(compressed_size - tarzstd_size) / tarzstd_size`; negative means smaller "
        "than plain TAR+ZSTD.",
        "",
        "`v2.1` and `v2.2` use `aggression_factor=1.0` for this report.",
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
            "## Confidence Buckets",
            "",
            "| Mode | Bucket | Cases | Metric A | Metric B |",
            "| ---- | ------ | ----: | -------: | -------: |",
        ]
    )
    for mode in ("v2.1", "v2.2"):
        confidence_summary = confidence_summaries[mode]
        lines.extend(
            [
                f"| `{mode}` | high_confidence_cases | "
                f"{int(confidence_summary['high_count'])} | "
                f"avg_delta {confidence_summary['high_avg_delta']:.2f}% | "
                f"win_rate {confidence_summary['high_win_rate']:.1f}% |",
                f"| `{mode}` | low_confidence_cases | "
                f"{int(confidence_summary['low_count'])} | "
                f"fallback_rate {confidence_summary['low_fallback_rate']:.1f}% | "
                f"worst_loss {confidence_summary['low_worst_loss']:.2f}% |",
            ]
        )
    lines.extend(
        [
            "",
            "## Per Dataset",
            "",
            "| Dataset | Mode | Selected | Delta vs TAR | Build time | encode_s | Confidence | Model quality | Structure score | Strong structure | Skipped builds | Prediction error |",
            "| ------- | ---- | -------- | -----------: | ---------: | -------: | ---------- | ------------: | --------------: | :--------------- | :------------- | ---------------: |",
        ]
    )
    for row in rows:
        for mode in ADAPTIVE_MODES:
            r = row[mode]
            assert isinstance(r, dict)
            error = r["prediction_error"]
            error_s = "" if error is None else f"{int(error):,}"
            model_quality = r["model_quality"]
            model_quality_s = (
                "" if model_quality is None else f"{float(model_quality):.3f}"
            )
            structure_score = r["structure_score"]
            structure_score_s = (
                "" if structure_score is None else f"{float(structure_score):.3f}"
            )
            lines.append(
                f"| {row['dataset']} | `{mode}` | `{r['selected']}` | "
                f"{float(r['delta_pct']):.2f}% | {float(r['build_time_s']):.4f}s | "
                f"{float(r['encode_s']):.4f}s | {r['confidence_band']} | "
                f"{model_quality_s} | {structure_score_s} | "
                f"{r['structure_signal_strong']} | {r['skipped_builds']} | {error_s} |"
            )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `v1` remains the exhaustive baseline: row + columnar v2 + columnar v1 are built.",
            "- `v2` is the first predictive selector.",
            "- `v2.1` uses explicit `entropy_estimate * size_weight + metadata_overhead_penalty + cardinality_penalty` scores, raw score-gap confidence, and records prediction error.",
            "- `v2.1` confidence-aware aggression: high score-gap builds only the best candidate; mid score-gap builds the top 2; risk cases fall back to TAR/safe mode.",
            "- `v2.2` adds deterministic structure-score sampling, a stable-structure columnar boost, and separate `prediction_confidence` vs `model_quality` metrics.",
            "",
        ]
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
