"""ZSTD level decision benchmark over the production-validation corpus suite.

Runs :func:`benchmarks.production_validation._dataset_specs` datasets and, for
each ZSTD level in ``{1, 2, 3}``, measures corpus-template (``.mck``) auto mode
with that level applied consistently to:

- MC template/columnar ZSTD streams
- the internal TAR+ZSTD baseline used for fallback sizing (fair comparison)

The per-file ZSTD baseline in ``production_validation`` stays at library default
(level 3); reported **delta vs TAR+ZSTD** uses the TAR+ZSTD archive built at the
same level as the MC row (see ``tar_zstd_level`` / ``mc_zstd_level``).

Does **not** change library defaults; reporting only.

Usage::

    python3 benchmarks/zstd_level_benchmark_generalization.py [--skip-very-large] \\
        [--exclude-datasets NAME,...]

The ``production_validation`` suite includes a **128MB** synthetic corpus
(``large_corpus_128mb``) and optionally **512MB+** (``very_large_corpus_512mb``).
Those dominate wall time; exclude them for interactive runs while still covering
the diverse edge corpora.

Writes ``results/metacompressor_zstd_level_generalization.json`` and a Markdown
summary alongside it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmarks import production_validation as pv  # noqa: E402

_RESULTS_DIR = REPO_ROOT / "results"
_JSON_NAME = "metacompressor_zstd_level_generalization.json"
_MD_NAME = "metacompressor_zstd_level_generalization.md"


def _mode_label(mode: str) -> str:
    return pv._mode_label(mode)


def _profile(spec: pv.DatasetSpec) -> str:
    return "%s / %s" % (spec.dataset_type, spec.realism)


def _measure_level(
    dataset_dir: Path,
    spec: pv.DatasetSpec,
    work_root: Path,
    level: int,
) -> Dict[str, Any]:
    """One dataset at one ZSTD level; raises ValidationError on correctness fail."""
    work_dir = work_root / ("%s_L%d" % (spec.name, level))
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    return pv._measure_dataset(
        dataset_dir,
        spec,
        work_dir,
        tar_zstd_level=level,
        mc_zstd_level=level,
        fast_zstd_level_sweep=True,
    )


def _extract_row(
    result: Dict[str, Any],
    level: int,
) -> Dict[str, Any]:
    """Flatten metrics for JSON/report."""
    tar_method = result["methods"]["tar_zstd"]
    mc_method = result["methods"]["mc_final_selected"]
    summary = result["mc_summary"]
    metrics = mc_method["metrics"]
    timing = metrics.get("timing") or {}
    mode = metrics["final_selected_mode"]
    delta = summary["delta_vs_tar_zstd_pct"]
    return {
        "zstd_level": level,
        "archive_bytes": mc_method["size"],
        "tar_zstd_bytes": tar_method["size"],
        "delta_vs_tar_zstd_pct": delta,
        "zstd_s": float(timing.get("zstd_s", 0.0)),
        "encode_s": float(mc_method["compress_s"]),
        "decode_s": float(mc_method["decompress_s"]),
        "selected_mode": mode,
        "selected_mode_label": _mode_label(mode),
        "structure_v2_enabled": metrics.get("structure_v2_enabled"),
        "fallback_raw_tar_zstd": bool(metrics.get("chose_raw_fallback")),
        "round_trip_ok": True,
    }


def _run_all(
    *,
    include_very_large: bool,
    exclude_names: frozenset[str],
) -> Dict[str, Any]:
    specs = [
        s
        for s in pv._dataset_specs(include_very_large=include_very_large)
        if s.name not in exclude_names
    ]
    levels = (1, 2, 3)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    by_dataset: Dict[str, Any] = {}
    flat_rows: List[Dict[str, Any]] = []

    tmp_root = Path(tempfile.mkdtemp(prefix="mc_zstd_level_gen_"))
    try:
        for spec in specs:
            ds_dir = tmp_root / spec.name
            print("dataset %s: generating..." % spec.name, flush=True)
            pv._build_dataset(ds_dir, spec)
            per_level: Dict[str, Any] = {}
            for level in levels:
                print(
                    "  level %d: compress + tar baseline + verify..." % level,
                    flush=True,
                )
                result = _measure_level(ds_dir, spec, tmp_root, level)
                row = _extract_row(result, level)
                row["dataset"] = spec.name
                row["profile"] = _profile(spec)
                per_level[str(level)] = row
                flat_rows.append(dict(row))
            by_dataset[spec.name] = {
                "spec": {
                    "dataset_type": spec.dataset_type,
                    "realism": spec.realism,
                    "structured": spec.structured,
                },
                "levels": per_level,
            }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Aggregates per level
    def level_stats(lvl: int) -> Dict[str, Any]:
        deltas: List[float] = []
        encodes: List[float] = []
        zstd_times: List[float] = []
        worst_delta: Optional[float] = None
        for name in by_dataset:
            r = by_dataset[name]["levels"][str(lvl)]
            d = r["delta_vs_tar_zstd_pct"]
            if d is not None:
                deltas.append(float(d))
                if worst_delta is None or d > worst_delta:
                    worst_delta = d
            encodes.append(float(r["encode_s"]))
            zstd_times.append(float(r["zstd_s"]))
        return {
            "worst_delta_vs_tar_zstd_pct": worst_delta,
            "avg_delta_vs_tar_zstd_pct": sum(deltas) / len(deltas) if deltas else None,
            "avg_encode_s": sum(encodes) / len(encodes) if encodes else None,
            "avg_zstd_s": sum(zstd_times) / len(zstd_times) if zstd_times else None,
            "dataset_count": len(by_dataset),
        }

    wins_size = {1: 0, 2: 0, 3: 0}
    wins_speed = {1: 0, 2: 0, 3: 0}
    for name in by_dataset:
        levels_map = by_dataset[name]["levels"]
        best_sz: Optional[Tuple[int, int]] = None  # (size, level)
        best_sp: Optional[Tuple[float, int]] = None  # (encode_s, level)
        for lvl in levels:
            r = levels_map[str(lvl)]
            sz = int(r["archive_bytes"])
            sp = float(r["encode_s"])
            if best_sz is None or sz < best_sz[0]:
                best_sz = (sz, lvl)
            if best_sp is None or sp < best_sp[0]:
                best_sp = (sp, lvl)
        if best_sz is not None:
            wins_size[best_sz[1]] += 1
        if best_sp is not None:
            wins_speed[best_sp[1]] += 1

    payload = {
        "generated_at": generated_at,
        "include_very_large": include_very_large,
        "excluded_datasets": sorted(exclude_names),
        "zstd_levels": list(levels),
        "note": (
            "MC and internal TAR+ZSTD baseline use the same zstd level per row; "
            "delta_vs_tar_zstd_pct compares mc_final_selected size to that baseline. "
            "zstd_per_file baseline uses level 3 (production_validation default). "
            "Sweep uses fast_zstd_level_sweep (TAR+ZSTD + MC auto only; single compress "
            "when verify_determinism=False)."
        ),
        "by_dataset": by_dataset,
        "rows": flat_rows,
        "aggregate_by_level": {str(lvl): level_stats(lvl) for lvl in levels},
        "wins_smallest_archive": wins_size,
        "wins_fastest_encode": wins_speed,
    }
    return payload


def _markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# ZSTD level sweep — generalization suite (corpus-template / .mck)",
        "",
        "Generated: `%s`" % payload["generated_at"],
        "",
        "**Levels:** %s. **Include very large (512MB+):** %s."
        % (payload["zstd_levels"], payload["include_very_large"]),
        "",
        "**Excluded datasets:** %s"
        % (
            ", ".join(payload["excluded_datasets"])
            if payload["excluded_datasets"]
            else "(none)"
        ),
        "",
        payload["note"],
        "",
        "## Aggregate by level",
        "",
        "| Level | Worst Δ vs TAR+ZSTD | Avg Δ vs TAR+ZSTD | Avg encode s | Avg zstd s |",
        "|---|---:|---:|---:|---:|",
    ]
    agg = payload["aggregate_by_level"]
    for lvl in payload["zstd_levels"]:
        a = agg[str(lvl)]
        lines.append(
            "| %d | %s | %s | %.4f | %.4f |"
            % (
                lvl,
                (
                    "n/a"
                    if a["worst_delta_vs_tar_zstd_pct"] is None
                    else "%.2f%%" % a["worst_delta_vs_tar_zstd_pct"]
                ),
                (
                    "n/a"
                    if a["avg_delta_vs_tar_zstd_pct"] is None
                    else "%.2f%%" % a["avg_delta_vs_tar_zstd_pct"]
                ),
                a["avg_encode_s"] or 0.0,
                a["avg_zstd_s"] or 0.0,
            )
        )
    lines.extend(
        [
            "",
            "**Smallest archive wins (count per level):** "
            + ", ".join(
                "L%d=%d" % (k, v)
                for k, v in sorted(payload["wins_smallest_archive"].items())
            ),
            "",
            "**Fastest encode wins (count per level):** "
            + ", ".join(
                "L%d=%d" % (k, v)
                for k, v in sorted(payload["wins_fastest_encode"].items())
            ),
            "",
            "## Per dataset",
            "",
            "| Dataset | Level | Archive B | Δ vs TAR+ZSTD | zstd_s | encode_s | decode_s | Mode | Profile | RT |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for row in sorted(payload["rows"], key=lambda r: (r["dataset"], r["zstd_level"])):
        d = row["delta_vs_tar_zstd_pct"]
        d_str = "n/a" if d is None else "%.2f%%" % d
        lines.append(
            "| %s | %d | %s | %s | %.4f | %.4f | %.4f | %s | %s | ok |"
            % (
                row["dataset"],
                row["zstd_level"],
                f"{row['archive_bytes']:,}",
                d_str,
                row["zstd_s"],
                row["encode_s"],
                row["decode_s"],
                row["selected_mode_label"],
                row["profile"],
            )
        )
    lines.extend(
        [
            "",
            "## Decision hints",
            "",
            "- If one level wins **both** size and speed counts clearly and aggregate "
            "deltas stay acceptable vs TAR+ZSTD, consider lowering default ZSTD level.",
            "- If wins split by dataset or worst-case Δ worsens at lower levels, keep "
            "level 3 default or add payload-aware selection.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ZSTD level 1/2/3 benchmark on production-validation datasets."
    )
    parser.add_argument(
        "--skip-very-large",
        action="store_true",
        help="Exclude the ~512MB synthetic corpus (faster).",
    )
    parser.add_argument(
        "--exclude-datasets",
        default="",
        help=(
            "Comma-separated production_validation dataset names to skip "
            "(e.g. large_corpus_128mb for faster interactive runs)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(_RESULTS_DIR),
        help="Directory for JSON/Markdown (default: results/).",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exclude_names = frozenset(
        name.strip() for name in args.exclude_datasets.split(",") if name.strip()
    )
    payload = _run_all(
        include_very_large=not args.skip_very_large,
        exclude_names=exclude_names,
    )
    json_path = out_dir / _JSON_NAME
    md_path = out_dir / _MD_NAME
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path.write_text(_markdown(payload), encoding="utf-8")

    print("Wrote %s" % json_path)
    print("Wrote %s" % md_path)
    print()
    for lvl in payload["zstd_levels"]:
        a = payload["aggregate_by_level"][str(lvl)]
        print(
            "L%d  avg Δ vs TAR+ZSTD=%s  worst=%s  avg encode=%.4fs  wins(size/speed)=%d/%d"
            % (
                lvl,
                (
                    "n/a"
                    if a["avg_delta_vs_tar_zstd_pct"] is None
                    else "%.2f%%" % a["avg_delta_vs_tar_zstd_pct"]
                ),
                (
                    "n/a"
                    if a["worst_delta_vs_tar_zstd_pct"] is None
                    else "%.2f%%" % a["worst_delta_vs_tar_zstd_pct"]
                ),
                a["avg_encode_s"] or 0.0,
                payload["wins_smallest_archive"][lvl],
                payload["wins_fastest_encode"][lvl],
            )
        )


if __name__ == "__main__":
    main()
