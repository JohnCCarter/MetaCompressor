# ADR 0001: Layout policy and CI guardrails

- **Status:** Accepted
- **Date:** 2026-05-04

## Context

The repository mixes library code, tests, benchmarks, generated reports, and editor/agent configuration. Without an explicit layout and checks, the tree tends to sprawl as the project grows.

## Decision

1. Document placement rules in **`docs/repository-layout-policy.md`** and enforce them for humans and agents via **`.cursor/rules/metacompressor-layout.mdc`**.
2. Add a **machine check** `scripts/check_repo_layout.py` that fails if any *git-tracked* `.py` file lies outside:
   - `metacompressor/*.py` (flat package; no `test_*.py` at package root),
   - `metacompressor/tests/**/*.py`,
   - `benchmarks/**/*.py`,
   - `scripts/**/*.py`.
3. On every push and pull request in **GitHub Actions** (`.github/workflows/ci.yml`), run that script, **`ruff check`**, **`black --check`** on `metacompressor/`, `scripts/`, and `benchmarks/`, and the **pytest** suite (see `[project.optional-dependencies] dev` in `pyproject.toml`).

## Consequences

- New Python modules **must** land under one of the allowed prefixes (or the check fails).
- Introducing a **nested package** under `metacompressor/` (e.g. `metacompressor/foo/bar.py`) requires updating the checker and this ADR—intentional friction.
- ADRs for future structural changes should reference whether the layout script needs updating.
