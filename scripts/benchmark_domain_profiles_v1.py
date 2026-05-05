#!/usr/bin/env python3
"""Benchmark domain profiles: generic vs logs and nginx."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from metacompressor.corpus_template import compress_corpus_template_with_metrics

PROFILES = ("generic", "logs", "nginx")


def _write(root: Path, files: Dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _bench(name: str, files: Dict[str, bytes]) -> Dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="mc_profile_bench_"))
    _write(root, files)
    row: Dict[str, object] = {"dataset": name}
    for profile in PROFILES:
        _, metrics = compress_corpus_template_with_metrics(
            root, adaptive="v2.2+pipeline", profile=profile
        )
        tar = int(metrics["tarzstd_size"])
        size = int(metrics["compressed_size"])
        row[profile] = {
            "size": size,
            "delta_pct": 0.0 if tar == 0 else 100.0 * (size - tar) / tar,
            "selected": metrics["selected_mode"],
        }
    return row


def _datasets() -> List[Tuple[str, Dict[str, bytes]]]:
    mixed_logs = {
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
    return [("mixed logs n=300", mixed_logs), ("nginx-like n=500", nginx_like)]


def main() -> None:
    rows = [_bench(n, f) for n, f in _datasets()]
    repo = Path(__file__).resolve().parents[1]
    out = repo / "results" / "metacompressor_domain_profiles_v1_benchmark.md"
    lines = [
        "# Domain profiles benchmark (adaptive=v2.2+pipeline)",
        "",
        "Compare generic vs logs profile and generic vs nginx profile.",
        "",
    ]
    for row in rows:
        lines.append(f"## {row['dataset']}")
        lines.append("")
        lines.append("| Profile | Selected mode | Delta % | Size |")
        lines.append("| ------- | ------------- | ------: | ---: |")
        for profile in PROFILES:
            r = row[profile]
            assert isinstance(r, dict)
            lines.append(
                f"| `{profile}` | `{r['selected']}` | {float(r['delta_pct']):.2f}% | {r['size']} |"
            )
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
