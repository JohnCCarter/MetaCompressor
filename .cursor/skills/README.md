# Skill governance charter (MetaCompressor)

Last update: 2026-05-04

## Purpose

This charter defines how **Cursor Agent Skills** (`SKILL.md` under `.cursor/skills/<name>/`) are governed in MetaCompressor so agents get **controlled, reusable workflows** without drifting product contracts.

Skills here are **Markdown + optional assets**, not JSON bundles and not runtime code shipped in the `metacompressor` wheel.

Authority for “what the product must never break” lives in **`AGENTS.md`**, **`pyproject.toml`**, and **accepted ADRs**—not in individual skills.

## Operational alignment and precedence

This charter governs **skill evolution** (what may change in a `SKILL.md` and how). Day-to-day coding constraints live in **`.cursor/rules/*.mdc`** and **`docs/policy/repository-layout-policy.md`**.

If instructions conflict, use repository precedence (see also **`AGENTS.md`** §9):

1. Explicit **user** request for the current task (when clear and lawful).
2. **`pyproject.toml`** (packaging, deps, tool config).
3. **Accepted `docs/adr/*.md`** when they explicitly decide behavior or structure.
4. **`AGENTS.md`** (governance, freeze zones).
5. **`docs/policy/METACOMPRESSOR_WORKING_CONTRACT.md`** (working anchor, stay-current checklist).
6. **`docs/policy/repository-layout-policy.md`** (where files live).
7. **`.cursor/rules/*.mdc`** (always-on / scoped agent rules).
8. **`.cursor/skills/*/SKILL.md`** (optional workflows—this layer).

There is **no** `.github/copilot-instructions.md` or `docs/OPUS_46_GOVERNANCE.md` in this repo; do not invent precedence above **`AGENTS.md`** unless those files are added and the hierarchy in **`AGENTS.md`** is updated.

### Quick path vs full diligence

- **Quick path** (small, localized edits) is allowed only when the change **cannot** affect freeze zones in **`AGENTS.md`** (e.g. typos in docs, comment-only edits).
- If a change might touch **compression wire format**, **container serialization**, **delta semantics**, or **documented CLI contracts**, use **full diligence**: tests, explicit user intent, and ADR where appropriate.

## Skill families (conceptual)

Skills in this repo are Markdown; classify intent as follows when authoring or reviewing.

### 1) SPEC-like (contract-aware)

Workflows that **encode verification** against a fixed contract (e.g. “run these tests before claiming lossless behavior”).

- Treat **test pass** and **documented behavior** as the authority—not the skill prose alone.
- Any skill text that **redefines** correctness must be rejected; fix **`AGENTS.md`**, ADRs, or tests instead.

### 2) RUNNER-like (execution / evidence)

Workflows that **run commands**, gather logs, or structure handoffs (e.g. delegate to `metacompressor-specialist`, run `pytest`, `ruff`, `black`).

- May add **non-breaking** steps, diagnostics, or reporting.
- Must **not** tell the agent to skip tests, ignore freeze zones, or weaken **`AGENTS.md`** constraints.

### 3) PLAYBOOK-like (guidance only)

Pure **how-to** or **checklists** with no claim to change PASS/FAIL semantics.

- No executable contract change.
- Prefer linking to **`docs/`** and **`AGENTS.md`** instead of duplicating governance prose.

## Scope principle

Allowed changes stay on **skill surfaces**: `SKILL.md` body, optional sibling files in the same skill folder, clearer descriptions, safer command templates.

Forbidden (without human + ADR path as needed):

- **Runtime default drift** (changing library defaults via skill instructions).
- **API / format contract drift** (skills “waiving” round-trip or determinism).
- **Scope broadening** (“this skill now allows editing container without tests”).
- **Weakening must-not rules** from **`AGENTS.md`** or from freeze sections.

## PASS stability and determinism

For MetaCompressor, **“PASS”** means what **CI and tests** enforce: lossless round-trip where promised, deterministic behavior where documented, linters green.

- Skills must **not** redefine PASS to match a broken implementation.
- If tests and a skill disagree, **tests + `AGENTS.md` win**; fix or delete the skill instruction.

## A′ evolution model (additive-only)

Allowed:

- Add steps, links, checklists, or delegation patterns.
- Tighten validation (e.g. “always run layout script before merge”).
- Improve failure messaging or evidence capture in the skill text.

Not allowed without explicit governance update:

- Redefining what counts as an acceptable change in freeze zones.
- Expanding what the skill authorizes the agent to skip or override.
- “Softening” must-not language found in **`AGENTS.md`**.

## Versioning (lightweight)

This repo does not version `SKILL.md` with SemVer files. Use **git history** as truth.

Treat skill edits like code review:

- **Small / additive** — clarify steps, fix links, add a checklist item: normal PR.
- **Behavioral** — skill now implies new obligations or skips checks: requires explicit reviewer awareness; if it touches product contracts, align **`AGENTS.md`** or an **ADR** first.

## Research isolation and promotion

Experimental workflows may live on a branch or in a **clearly named** draft skill folder.

Promotion:

1. Validate against **CI** (`ci.yml`) locally or in PR.
2. Keep instructions **deterministic** (same commands, same paths from repo root).
3. Reconcile with **`AGENTS.md`** and **`docs/policy/repository-layout-policy.md`**.
4. Merge without contradicting locked freeze semantics.

## Ambiguity rule

If a skill’s role is unclear, treat it as **SPEC-like (strict)**: do not skip tests or freeze-related diligence; add a **TODO** in the skill or open a discussion to reclassify.

## Index of skills

| Skill | Role |
|-------|------|
| [`metacompressor-collab`](metacompressor-collab/SKILL.md) | Main agent ↔ `metacompressor-specialist` handoffs and checklists |

Add new rows here when you add skills.
