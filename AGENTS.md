# AGENTS.md — Agent governance (MetaCompressor)

## Last update: 2026-05-08

This file is the **stable governance layer** for any automated or semi-automated agent working in this repository (e.g. Cursor Agent, subagents, future CI agents). It defines **boundaries and authority**—not day-to-day tactics.

It is **not** a substitute for `pyproject.toml`, tests, or the repository layout policy.

## 1) Separation of responsibility

| Layer | Role |
|--------|------|
| **`AGENTS.md`** (this file) | Stable mandates: what agents must not break, how conflicts are ordered, freeze-sensitive areas. |
| **`docs/adr/*.md`** | Recorded **architecture decisions**; supersede informal habit when they explicitly decide structure or behavior. |
| **`docs/policy/repository-layout-policy.md`** | Where files belong, naming and splitting conventions. |
| **`.cursor/rules/*.mdc`** | Editor/agent **constraints** (always-on or scoped); operational but subordinate to this file and ADRs. |
| **`.cursor/skills/*/SKILL.md`** | **On-demand workflows** (how to run checks, delegate subagents, etc.). **Not** a runtime execution surface and **not** a source of product truth. Governance charter: **`.cursor/skills/README.md`**. |
| **`benchmarks/`** + **`results/`** | Validation and **reports**; they do **not** override unit-test contracts in `metacompressor/tests/`. |

Skills and rules **do not** ship in the Python wheel; they guide development only.

## 2) Scope of this document (governance-only)

This document **must** only define:

- Agent mandates and non-negotiable product constraints (at a high level).
- Governance principles (evidence, scope discipline, determinism).
- Freeze-sensitive **areas** of the codebase / format (by name, not by ticket).
- What Cursor **skills** and **rules** may *not* do relative to this file.
- Authority hierarchy when documents disagree.

This document **must not** contain:

- Daily work logs or chat transcripts.
- PR-specific checklists (put those in PR templates or skills).
- One-off benchmark audit narratives (link to `results/` or ADRs instead).
- Self-mutation instructions (“rewrite AGENTS.md from a skill”) unless a human explicitly approves that change.

## 3) Agent roles (generic)

This repository does **not** prescribe named vendor models. Any agent acting here should behave as:

- **Implementer** — writes and edits code within scope; runs tests and linters; follows layout policy.
- **Reviewer / auditor** — challenges behavior changes, demands evidence (tests, diffs), flags freeze violations.

Both roles must **preserve lossless round-trip** and **deterministic** behavior for the `.mc1` pipeline wherever the format promises it, unless the user and/or an ADR explicitly approves a breaking change.

## 4) Governance principles

- **Default:** **no behavior change** to compression, container serialization, or CLI contracts unless explicitly requested or covered by an accepted ADR.
- **Scope discipline:** for non-trivial work, be explicit about what is in scope and out of scope before large edits.
- **Evidence:** correctness claims require **tests** (and benchmarks only as supporting evidence, not as the sole spec).
- **Determinism and contract stability** take precedence over convenience refactors.

## 5) Freeze-sensitive zones

Changes in these areas need **extra scrutiny** (tests, ADR, or explicit user sign-off as appropriate):

- **`metacompressor/compressor.py`**, **`decompressor.py`**, **`container.py`**, **`delta.py`** — wire format, chunking, deduplication, delta encoding, serialization.
- **Public CLI behavior** documented as stable (`mc` entry in `pyproject.toml`, user-visible flags and exit codes where documented).
- **Published on-disk `.mc1` semantics** and backward compatibility expectations.

Cursor **skills** and **rules** must **not** be used to bypass these constraints (e.g. “skip tests for this freeze zone”).

## 6) Skill model (additive-only)

- Skills under **`.cursor/skills/`** may add **workflows, checklists, and delegation patterns** only.
- They must **not** redefine “PASS”, alter determinism guarantees, or broaden constitutional mandates defined here.
- They must **not** instruct agents to **override** `pyproject.toml`, accepted ADRs, or this file.

## 7) Skills: allowed operational content

`SKILL.md` files may describe:

- Preconditions and validation steps (e.g. run `pytest`, `ruff`, `black`).
- Review checklists and handoff patterns between main agent and subagents.
- Links to `docs/policy/repository-layout-policy.md` and ADRs.

All such content remains **subordinate** to this file and to **accepted ADRs**.

## 8) Skills: prohibited actions

Skills must **never**:

- Override this governance file or freeze rules.
- Introduce new product mandates without human alignment.
- Treat **`results/`** reports as replacing **`metacompressor/tests/`**.
- Encourage implicit self-modification of governance files or ADRs.

## 9) Hierarchy of authority (product + repo)

When documents or instructions conflict, use this order **from strongest to weakest** for **product truth and governance**:

1. **Explicit user instruction** for the current task (when clear and lawful).
2. **`pyproject.toml`** — dependencies, entrypoints, declared tooling config.
3. **Accepted `docs/adr/*.md`** — decisions explicitly marked accepted that bear on the conflict.
4. **`AGENTS.md`** (this file).
5. **`docs/policy/METACOMPRESSOR_WORKING_CONTRACT.md`** — working anchor and stay-current checklist (must not contradict this file).
6. **`docs/policy/repository-layout-policy.md`** — file placement and structure.
7. **`.cursor/rules/*.mdc`** — operational agent rules.
8. **`.cursor/skills/*/SKILL.md`** — optional workflows.

**`README.md`** sets project intent and pointers; it does not override `pyproject.toml` on packaging facts.

## 10) References (this repository)

- `README.md`
- `pyproject.toml`
- **`docs/policy/METACOMPRESSOR_WORKING_CONTRACT.md`** — rolling anchor / checklist for staying current (subordinate to this file).
- `docs/policy/repository-layout-policy.md`
- `docs/adr/README.md`
- `.cursor/rules/`
- `.cursor/skills/` (see **`.cursor/skills/README.md`** — skill charter)
- `.github/workflows/ci.yml` — automated checks (layout, Ruff, Black, pytest).

There is **no** `.github/copilot-instructions.md`, **no** `docs/OPUS_46_GOVERNANCE.md`, and **no** `.github/skills/*.json` in this repo; add them to this list **and** to section 9 if you introduce equivalents later.

## Cursor Cloud specific instructions

This is a self-contained Python library with **no external services** (no databases, Docker, or APIs). The update script runs `pip install -e ".[dev]"` which installs the package in editable mode with all dev tooling.

**Caveats:**
- Use `python3` (not `python`) — the VM may not have a `python` symlink.
- Standard dev commands are documented in `README.md` and the `.cursor/rules/metacompressor-workflow.mdc` rule. Refer to those for lint (`ruff check`), format check (`black --check`), test (`python3 -m pytest metacompressor/tests -q`), and CLI (`mc --help`) commands.
- 8 tests are routinely skipped (marker-gated `medium`/`large` tests or optional-dependency tests); this is normal.
- Use `-m small` for a faster subset; `medium`/`large` markers require explicit opt-in (`RUN_LARGE_TESTS=1` for large).
- Temp/scratch files go in `mc_test_output/` or `tmp_*` (gitignored). Never commit generated blobs.
