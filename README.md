# MetaCompressor

Deterministic lossless compression (chunking, deduplication, Zstandard). See `pyproject.toml` for install and CLI entry `mc`. For lint/tests locally: `pip install -e ".[dev]"` then `ruff check metacompressor scripts benchmarks`, `black --check metacompressor scripts benchmarks`, and `pytest metacompressor/tests`. Git hooks: `pre-commit install && pre-commit install --hook-type pre-push` then `pre-commit run --all-files` (and pre-push runs full pytest via `scripts/run_precommit_pytest.py`).

## Adoption quickstart (safe defaults)

MetaCompressor defaults are designed to be conservative and deterministic:

- experimental shaping/substitution paths are OFF by default
- fallback stays fail-closed and explicit
- reported mode/ratio/timings are intended to be explainable, not opaque

Start with:

- `mc compare-dir <input_dir>`

This command provides:

- selected path
- ratio and delta vs TAR+ZSTD
- speed summary
- fallback summary/reasons
- workload guidance hint

## Workload guidance

Use MC first on datasets that match these patterns:

- structured logs
- JSON/NDJSON corpora
- repeated templates across many files
- many-small-files repositories

Use extra caution (and compare against TAR+ZSTD baseline) for:

- noisy/low-structure corpora
- high-entropy/random-like bytes
- already compressed assets

Evidence references:

- product benchmark summary: `results/phases/phase5_product_benchmark_suite.md`
- hotpath optimization summary: `docs/planning/template-hotpath-optimization-summary.md`
- adoption plan: `docs/planning/phase6-adoption-mode-plan.md`

## Repository layout

**Working contract (stay current):** **[docs/policy/METACOMPRESSOR_WORKING_CONTRACT.md](docs/policy/METACOMPRESSOR_WORKING_CONTRACT.md)**. Governance: **[AGENTS.md](AGENTS.md)**. Layout: **[docs/policy/repository-layout-policy.md](docs/policy/repository-layout-policy.md)**. ADRs: **[docs/adr/](docs/adr/README.md)**. CI: **`.github/workflows/ci.yml`**.
