#!/usr/bin/env python3
"""Generate results/metacompressor_adaptive_selection_v1.md from micro-benchmark corpora."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

import metacompressor.corpus_template as ct
from metacompressor.corpus_template import (
    _ADAPT_COL_V1,
    _ADAPT_COL_V2,
    _ADAPT_ROW,
    _ADAPT_TAR,
    compress_corpus_template_with_metrics,
)


def _write_corpus(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _run_dataset(name: str, files: Dict[str, bytes]) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_adapt_"))
    _write_corpus(root, files)
    old_th = ct._CORPUS_FALLBACK_THRESHOLD
    ct._CORPUS_FALLBACK_THRESHOLD = float("inf")
    try:
        _, metrics = compress_corpus_template_with_metrics(root)
    finally:
        ct._CORPUS_FALLBACK_THRESHOLD = old_th

    cs = metrics["candidate_sizes"]
    tar_plain = int(metrics["tarzstd_size"])
    row_b = int(cs[_ADAPT_ROW])
    col_best = min(int(cs[_ADAPT_COL_V1]), int(cs[_ADAPT_COL_V2]))
    selected = str(metrics["selected_mode"])
    final_b = int(metrics["compressed_size"])
    delta_vs_tar = final_b - tar_plain

    if selected == _ADAPT_TAR:
        winner = "TAR+ZSTD (MCK)"
    elif selected == _ADAPT_ROW:
        winner = "Row template"
    elif selected in (_ADAPT_COL_V1, _ADAPT_COL_V2):
        winner = "Columnar"
    else:
        winner = selected

    return {
        "dataset": name,
        "tar_plain": tar_plain,
        "row": row_b,
        "columnar_best": col_best,
        "selected": selected,
        "winner": winner,
        "delta_vs_tar": delta_vs_tar,
        "final_mode": metrics["final_selected_mode"],
    }


def _datasets() -> List[Tuple[str, Dict[str, bytes]]]:
    line = b'{"service":"api","status":200,"request_id":"user-%d","path":"/p"}\n'
    prefixed_small = {
        "x.ndjson": b"".join(b"2026-05-04T12:00:00Z " + (line % i) for i in range(50))
    }
    plain_small = {"x.ndjson": b"".join(line % i for i in range(50))}
    plain_large = {"x.ndjson": b"".join(line % i for i in range(500))}
    mixed = {
        "a.log": b"".join(
            (
                b"2026-01-01T00:00:00Z service=api "
                b"trace=%016x latency_ms=%d\n" % (i, i % 200)
            )
            for i in range(120)
        )
    }
    nginx = {
        "access.log": b"".join(
            (
                b'127.0.0.1 - - [01/Jan/2026:12:00:%02d +0000] "GET /path/%d HTTP/1.1" 200 %d\n'
                % (i % 60, i, 100 + (i % 50))
            )
            for i in range(200)
        )
    }
    highcard = {
        "h.log": b"".join(
            b"INFO id=%032x msg=ok\n" % (i * 7919 + 17,) for i in range(150)
        )
    }
    many_small: Dict[str, bytes] = {}
    for i in range(80):
        many_small[f"shard/{i:04d}.txt"] = b"OK row=%d\n" % i

    structured = {
        "s.log": b"".join(
            f"INFO seq={i} status={i % 5} user={i % 9}\n".encode() for i in range(200)
        )
    }

    return [
        ("prefixed NDJSON n=50", prefixed_small),
        ("plain NDJSON n=50", plain_small),
        ("plain NDJSON n=500", plain_large),
        ("mixed microservice-like", mixed),
        ("nginx-like access n=200", nginx),
        ("high-cardinality ids n=150", highcard),
        ("many-small-files n=80", many_small),
        ("structured logs n=200", structured),
    ]


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    out_path = repo / "results" / "metacompressor_adaptive_selection_v1.md"

    rows: List[Dict[str, object]] = []
    for title, files in _datasets():
        rows.append(_run_dataset(title, files))

    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not medium and not large",
        str(repo / "metacompressor" / "tests"),
        "-q",
        "--tb=no",
    ]
    proc = subprocess.run(pytest_cmd, cwd=str(repo), capture_output=True, text=True)
    ok = proc.returncode == 0
    verdict = (
        "ADAPTIVE_SELECTION_V1_VALIDATED\n"
        if ok
        else (
            "ADAPTIVE_SELECTION_V1_PARTIAL\n" f"Reason: pytest exit {proc.returncode}\n"
        )
    )

    lines = [
        "# MetaCompressor adaptive selection v1",
        "",
        "**Before:** pick the smaller of row template vs columnar v2, then swap to "
        "TAR+ZSTD-in-MCK if that archive exceeded ``_CORPUS_FALLBACK_THRESHOLD × tarzstd_size``.",
        "",
        "**After (v1):** build row, columnar v2, columnar v1, and TAR+MCK; drop row/columnar "
        "candidates that fail the same threshold gate vs ``tarzstd_size``; choose the smallest "
        "remaining final ``.mck`` with deterministic tie-break (row, then v2, v1, TAR).",
        "",
        "Table columns: **TAR+ZSTD** = plain corpus TAR+ZSTD bytes; **Row** / **Columnar** = "
        "full ``.mck`` sizes for that encoding; **Selected** = adaptive winner; "
        "**Delta vs TAR+ZSTD** = ``compressed_size − tarzstd_size`` (negative means smaller than plain TAR+ZSTD).",
        "",
        "| Dataset | TAR+ZSTD | Row | Columnar | Selected | Winner | Delta vs TAR+ZSTD |",
        "| ------- | -------: | --: | -------: | -------- | ------ | ----------------: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['dataset']} | {r['tar_plain']:,} | {r['row']:,} | "
            f"{r['columnar_best']:,} | `{r['selected']}` | {r['winner']} | "
            f"{r['delta_vs_tar']:,} |"
        )
    lines.extend(
        [
            "",
            "## Pytest (fast suite)",
            "",
            "```text",
            (proc.stdout + proc.stderr).strip() or "(no output)",
            "```",
            "",
            "```text",
            verdict.rstrip(),
            "```",
            "",
        ]
    )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
