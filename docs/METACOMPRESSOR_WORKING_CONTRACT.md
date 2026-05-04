# MetaCompressor — working contract

Last update: 2026-05-04
Status: active

## What this is

A **short, stable anchor** for humans and agents: what to honour, where to read the full rules, and how to **stay current** after pulls or policy changes.

This file is **not** the constitution. If anything here disagrees with **`AGENTS.md`**, **`pyproject.toml`**, or an **accepted ADR**, those win—then update **this** document in the same change.

## Core commitments (non-exhaustive)

1. **Lossless round-trip** where the format promises it: `compress` → `decompress` → identical bytes for supported inputs.
2. **Determinism** where documented; no “helpful” randomness in the wire path without an ADR.
3. **No new PyPI dependencies** without explicit user/product approval (`pyproject.toml` is the source of truth).
4. **Layout**: follow **`docs/repository-layout-policy.md`**; Python path rules are enforced by **`scripts/check_repo_layout.py`** in CI.
5. **Quality bar before claiming done**: relevant **`pytest`**, **`ruff check`**, **`black --check`** (see **`.github/workflows/ci.yml`** and **`.pre-commit-config.yaml`**).
6. **Freeze zones** (compression path, container, delta, documented CLI): no silent behaviour change—see **`AGENTS.md`** §5.

## Authority & depth (where to read more)

Order matches **`AGENTS.md`** §9; this table is for navigation only.

| Topic | Canonical document |
|--------|----------------------|
| Governance, freeze zones, skills vs constitution | **`AGENTS.md`** (repo root) |
| File placement, packages, splits | **`docs/repository-layout-policy.md`** |
| Recorded architecture decisions | **`docs/adr/*.md`** + **`docs/adr/README.md`** |
| Cursor skill charter (A′ additive) | **`.cursor/skills/README.md`** |
| Day-to-day agent commands & hooks | **`.cursor/rules/metacompressor-workflow.mdc`** |
| CI gates | **`.github/workflows/ci.yml`** |
| Local git hooks | **`.pre-commit-config.yaml`** + **`scripts/run_precommit_pytest.py`** |

## Stay up to date (agents & humans)

After **`git pull`** or when picking up a stale branch, **re-read** (skim is OK if unchanged in git log):

1. **`docs/METACOMPRESSOR_WORKING_CONTRACT.md`** (this file) — delta in “Last update” or short changelog below.
2. **`AGENTS.md`** — especially §5 freeze zones and §9 precedence if new ADRs landed.
3. **`docs/repository-layout-policy.md`** — if folders or layout checks changed.
4. **`.github/workflows/ci.yml`** — new or renamed CI steps.
5. **`.pre-commit-config.yaml`** — new hooks or stages.

If **`Last update`** at the top of this file moved, treat the contract as **touched** and reconcile your assumptions with the table above.

## Session checklist (before large edits)

- [ ] Confirm goal vs **freeze** zones (`AGENTS.md` §5).
- [ ] Choose correct **zone** for new files (layout policy + `check_repo_layout.py`).
- [ ] Run or plan **`pytest`**, **`ruff check metacompressor scripts benchmarks`**, **`black --check`** (or `pre-commit run --all-files`).
- [ ] For delegation: **`@metacompressor-collab`** and **`metacompressor-specialist`** subagent when appropriate.

## Changelog (contract only)

| Date | Change |
|------|--------|
| 2026-05-04 | Initial working contract: pointers to `AGENTS.md`, layout policy, ADR, CI, pre-commit, skills charter. |

When you change governance, CI, or layout rules, add a **one-line row** here in the same PR.

## Updating this contract

- **Small** (links, typos, checklist wording): normal PR.
- **Semantic** (new freeze, new CI gate, new authority order): update **`AGENTS.md`** and/or **ADR** first, then align this file and the changelog row.
