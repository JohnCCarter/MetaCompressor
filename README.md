# MetaCompressor

Deterministic lossless compression (chunking, deduplication, Zstandard). See `pyproject.toml` for install and CLI entry `mc`. For lint/tests locally: `pip install -e ".[dev]"` then `ruff check metacompressor scripts benchmarks`, `black --check metacompressor scripts benchmarks`, and `pytest metacompressor/tests`. Git hooks: `pre-commit install && pre-commit install --hook-type pre-push` then `pre-commit run --all-files` (and pre-push runs full pytest via `scripts/run_precommit_pytest.py`).

## Benchmarks

**Acceptance hardening** (Win-rate / Speed / Memory / Trust report): from repo root run `python benchmarks/acceptance_hardening.py` (optional `--output-dir`; default `results/`). Writes `metacompressor_acceptance_hardening.md` and `metacompressor_acceptance_hardening.json`. Set `RUN_LARGE_TESTS=1` to include the `structured_scale_500mb` dataset.

## Repository layout

**Working contract (stay current):** **[docs/METACOMPRESSOR_WORKING_CONTRACT.md](docs/METACOMPRESSOR_WORKING_CONTRACT.md)**. Governance: **[AGENTS.md](AGENTS.md)**. Layout: **[docs/repository-layout-policy.md](docs/repository-layout-policy.md)**. ADRs: **[docs/adr/](docs/adr/README.md)**. CI: **`.github/workflows/ci.yml`**.
