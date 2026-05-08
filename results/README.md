# `results/` layout

Committed benchmark and validation **reports** only (not behavioral spec). Drivers live under `benchmarks/`; stale iterative snapshots may live under `archive/`.

| Subfolder | Contents |
|-----------|----------|
| `phases/` | Phase rollout evidence (`phase1`–`phase5`, JSON + Markdown). |
| `differential/` | Differential path gates, hit-rate, and related notes. |
| `hardening/` | Acceptance hardening JSON/MD, stress suite MD, internal hardening MD. |
| `product/` | Production validation, structure v2, edge/MVP/CDC/columnar reports. |
| `corpus/` | Ad hoc corpus benchmark artifacts (e.g. delta vs baseline, warm-path JSON). |

Default output directories for repo-root runs are set in the corresponding `benchmarks/*.py` modules. Override with each driver’s `--output-dir` / equivalent when needed.
