# Repository Layout Policy

Last update: 2026-05-04
Status: active

## Purpose

This document defines practical layout and file-placement guidance for **MetaCompressor**. It exists to reduce sprawl, improve discoverability, and make future refactors easier for both humans and agents.

This is a **repository-structure policy**, not a substitute for correctness requirements (lossless round-trip, tests) or packaging rules in `pyproject.toml`.

## Relationship to higher-order sources

This document is **subordinate** to project facts and decisions recorded elsewhere. In case of conflict, resolve in this order:

1. **`pyproject.toml`** — package name, dependencies, declared entrypoints, pytest configuration.
2. **`README.md`** — project intent and onboarding pointers.
3. **Accepted `docs/adr/*.md`** — architecture decisions that explicitly change layout, structure, or behavior.
4. **`AGENTS.md`** — stable agent governance (mandates, freeze zones, authority over skills/rules).
5. **`docs/METACOMPRESSOR_WORKING_CONTRACT.md`** — working anchor and “stay up to date” checklist (subordinate to `AGENTS.md`).
6. **This file** (`docs/repository-layout-policy.md`) — repository layout and placement.
7. **`.cursor/rules/*.mdc`** — operational agent constraints (testing, layout summary, etc.).

Other global instruction files (e.g. Copilot instructions) are **not** present unless added to the repo; if added, extend **`AGENTS.md`** hierarchy and reference them there.

This policy focuses on **where** content should live, **how** Python modules should be split, and **when** to prefer folders, sibling modules, or local helpers—mapped to MetaCompressor’s actual tree.

## Scope

This policy applies to the **whole repository**, with different strictness by zone.

### Primary layout zones (strict)

| Zone | Role |
|------|------|
| `metacompressor/` | Installable library source (flat package today: top-level `*.py` modules). |
| `metacompressor/tests/` | Pytest suite only. |
| `benchmarks/` | Long-running validation, acceptance, and benchmark **drivers** (may import `metacompressor`). |
| `scripts/` | Repo maintenance and CI helpers (e.g. layout verification). **Not** shipped in the wheel. |

### Secondary support zones (clear taxonomy)

| Zone | Role |
|------|------|
| `docs/` | Policies, ADRs (`docs/adr/`), architecture notes, contributor guides. |
| `.github/` | GitHub Actions and repository automation only. |
| `.cursor/` | Cursor rules, skills, commands, custom subagents (editor/agent config). |
| `.vscode/` | Shared workspace editor settings. |

**Optional / not used yet:** If the project later adds `tools/` (developer utilities beyond `scripts/`), `config/` (schemas and non-code defaults), `data/` (curated fixtures), or `registry/` (metadata catalogs), introduce them deliberately and extend this document—do not sprawl at repo root.

### Output and hygiene zones

| Zone | Role |
|------|------|
| `results/` | Generated benchmark / validation reports (`*.md`, `*.json`, …). Snapshots may be committed; they are **not** the behavioral spec. |
| `mc_test_output/`, `tmp_metacompressor_test/` | Local CLI/test scratch — **must stay gitignored** (see `.gitignore`); never commit generated `.mc1` / `.restored` blobs here. |

**Optional / not used yet:** `artifacts/`, `logs/`, `cache/`, `archive/`—if added, treat like hygiene zones (containment, naming, lifecycle), not homes for active library code.

### Machine enforcement (Python only)

Tracked `*.py` paths are validated by **`scripts/check_repo_layout.py`** in CI (see `docs/adr/0001-layout-policy-and-ci-guardrails.md`). That check does **not** cover Markdown, JSON, or other assets—those follow this document and review discipline.

This policy does **not** define: runtime compression semantics, merge approval, or freeze rules.

## Goals

The layout should make it easy to answer quickly:

1. Where does this **behavior** belong (`compress`, container, CLI, tests, benchmarks)?
2. What is the **public entrypoint** (`metacompressor` API, `mc` CLI)?
3. Which files are **internal library** vs **drivers** vs **generated output**?
4. Where do **tests**, **scripts**, **docs**, and **reports** live relative to that?

## Zone model (MetaCompressor)

### `metacompressor/` (library)

- **Only** runtime code for the published package and shared types/helpers used by that package.
- **No** `test_*.py` at package root—tests live under `metacompressor/tests/`.
- **No** benchmark drivers here—use `benchmarks/`.
- **CLI** is centralized in `cli.py` with clear subcommand structure.

### `metacompressor/tests/`

- **`test_<area>.py`** aligned with features (`test_compressor.py`, `test_delta.py`, …).
- Prefer **round-trip** and regression tests; respect pytest **markers** (`small`, `medium`, `large`) per `pyproject.toml`.
- **Do not** import `benchmarks/` as if it were public API unless the test explicitly validates a driver contract—prefer subprocess or thin integration boundaries.

### `benchmarks/`

- Scripts and modules for **stress**, **acceptance**, and **production-like** validation.
- May import `metacompressor`; the library must **not** import `benchmarks/` at runtime.
- Write human-readable and machine summaries under **`results/`** (or a CLI-provided output directory).

### `scripts/`

- Small helpers run locally or in CI (e.g. layout checks). **Stdlib-first** when possible.
- **Not** part of the installable package (see `pyproject.toml` package discovery excludes).

### `docs/` and `docs/adr/`

- **Policies** (this file), **ADRs** (decisions), architecture notes.
- Do not store **generated** benchmark prose as the canonical spec—link to `results/` or summarize intentionally here.

### `.github/`

- **Workflows** and GitHub-specific automation only—not a general doc dump.

### `results/`

- **Reports** from benchmark runs. Naming should stay predictable; content is not a substitute for unit tests.

## Core principles

### Place files by domain before by implementation style

Prefer grouping by **compression domain** (chunking, container, delta, CLI, corpus) over vague labels.

Good:

- `metacompressor/compressor.py`
- `metacompressor/container.py`
- `metacompressor/delta.py`

Less good:

- Cross-domain dumping into generic `helpers.py`, `utils.py`, `misc.py`, or `temp.py` unless narrowly scoped and documented.

### Keep one clear public entrypoint per area when possible

- Library: stable symbols via `metacompressor/__init__.py` only when intentionally public.
- CLI: `mc` → `metacompressor.cli:main`.

### Split only when the new boundary is meaningful

Do not split only for line count. Split when the extracted code has a **stable role**: distinct sub-flow, coherent algorithm family, reusable internal utilities for one domain, or separate reporting/metrics concerns.

### Avoid wrapper inflation

Avoid files that only re-export without a boundary, rename without clarifying responsibility, or fragment into one-function-per-file without navigation benefit.

### Keep the repository root intentional

Root should hold **manifests** (`pyproject.toml`, `README.md`), top-level **zones** (`metacompressor/`, `benchmarks/`, `docs/`, …), and editor/agent config (`.cursor/`, `.vscode/`). New domain code belongs under the appropriate zone, not loose at root.

## Terminology

### Orchestrator module

Coordinates a local flow and is the obvious entrypoint (e.g. `metacompressor/cli.py` for commands, `compressor.compress` as the compression entry path).

### Parts package (`_parts/`)

Use when **one** parent area grows multiple internal clusters, the parent remains the natural entrypoint, and sibling files would become noisy. **Not** the default for every split.

Example (if the package grows): `metacompressor/container_parts/` with `container.py` as the façade—only when justified.

### Component module

A sibling with a **named responsibility** and understandable boundary, e.g. `delta.py` alongside `compressor.py`.

### Helper module

Narrow local support for one domain (e.g. small helpers colocated or `*_helpers.py` **only** if it stays narrow). If it becomes a junk drawer, split or rename by domain.

## Layout rules for `metacompressor/`

1. **Keep modules near the owning domain** — chunking, serialization, delta, corpus, logging templates, etc.
2. **Prefer descriptive siblings** over `utils_more.py` — e.g. clearer names tied to behavior.
3. **`_parts/`** only when multiple internal clusters justify a subpackage; otherwise prefer siblings.
4. **Keep private support local** before promoting to a shared catch-all.
5. **Avoid anonymous generic folder/module names** (`helpers`, `utils`, `common`, `misc`, `temp`, `new`, `old`) unless extremely local and temporary by design.

## Layout rules for `benchmarks/` and `results/`

1. **Drivers** and validation logic live in `benchmarks/`; **outputs** go to `results/` (or CLI-specified dirs).
2. **Do not** treat `results/` as the behavioral specification—tests in `metacompressor/tests/` define correctness contracts.
3. **Naming** should reflect the script or validation type (e.g. `acceptance_hardening.py`, `production_validation.py`).

## Layout rules for `scripts/`

1. **Canonical** location for operational repo scripts—avoid leaving active `.py` utilities at repo root.
2. **Group by purpose** (layout check, future migration helpers, codegen)—not by author.
3. **Task-oriented names** (`check_repo_layout.py`)—avoid `run_once.py`, `tmp_fix.py`.

## Layout rules for `metacompressor/tests/`

1. Tests **follow the domain** they validate; prefer existing `test_*.py` naming patterns.
2. **Mirror responsibility**, not every private seam—extend an existing test file when the contract is unchanged; add a new file when behavior or risk warrants it.
3. Prefer **clear structure over time** (markers, focused modules); avoid unbounded root sprawl if subfolders become useful later—document in this file when introduced.

## Layout rules for `docs/`, `.github/`, `.cursor/`

1. **`docs/`** — coarse taxonomy: policy, ADR, architecture, onboarding.
2. **Keep policy distinct** from ADR content: layout guidance here; decisions that change structure in `docs/adr/`.
3. **`.github/`** — automation only (workflows, issue templates if added).
4. **`.cursor/`** — rules, skills (see **`.cursor/skills/README.md`** for skill charter), commands, agents—not runtime library code.

## Layout rules for output and hygiene zones

1. **Generated outputs** stay out of `metacompressor/` and `metacompressor/tests/`.
2. **Distinguish** curated docs vs generated reports (reports → `results/`).
3. **Temporary** work → gitignored dirs (`tmp_*`, `mc_test_output/`); promote to a durable home only when intentional.
4. **Do not** let `results/` become an undocumented junk drawer—keep names and purposes visible.

## Layout rules for the repository root

1. Root hosts **control files** and **top-level zones** only.
2. **Avoid root drift** — no new loose scripts, tests, or dumps when a zone exists.
3. **No “temporary at root”** that becomes permanent—pick the proper folder early.

## When to choose each structure

- **Single file** — flow still readable end-to-end; extraction would be noise.
- **Sibling modules** — distinct, nameable responsibilities (`compressor.py`, `decompressor.py`, `delta.py`).
- **Subfolder / subpackage** (e.g. `metacompressor/foo/bar.py`) — only when several modules form **one cohesive area** (shared imports, one clear façade, tests that naturally group) and **flat siblings** would be noisy or ambiguous. **Do not** create packages early just because there are “multiple files in the same category”—two or three well-named siblings at the package root are usually better than an empty-looking folder hierarchy. Rule of thumb: consider a subpackage when **~4+** closely related modules *or* cohesion/import pain appears—whichever comes first. **Today** CI allows only **flat** `metacompressor/*.py`; introducing nested packages requires updating **`scripts/check_repo_layout.py`** and this document (and usually an ADR).
- **`_parts/` package** — one owner module, several internal clusters, siblings would be ambiguous.
- **Helper file** — small, local support; a package would be overkill.
- **New top-level zone** — durable category that improves navigation (document here when added).

## When to split a module

**Split when:** clear sub-domain, multiple responsibilities hurting readability, extracted parts get **clearer names**, or regression risk is easier to manage with boundaries.

**Line count** (~500–800 lines) is a **warning**, not a rule alone.

**Do not split when:** only a tiny extraction, wrappers with no domain value, weaker names, or complexity just moves sideways.

## Anti-patterns

Unless documented and justified, avoid:

- One-function-per-file fragmentation without navigation benefit
- Generic helper dumping grounds
- Duplicate mapping layers and pointless wrappers
- `*_v2`, `*_new`, `*_final` naming drift
- Root-level script or test clutter
- Writing benchmark reports into `metacompressor/` as if they were source
- Scattering config without an owning folder (use `config/` only if introduced with clear authority)

## Review checklist (before adding a module or folder)

1. Which **domain** owns this (compression path, CLI, corpus, container, …)?
2. Is this a **real role**, a **local helper**, or only **line-count extraction**?
3. Would a **descriptive sibling** beat a generic helper name?
4. Is a **`_parts/`** package justified by multiple internal clusters?
5. Should tests **extend an existing file** or a **new `test_*.py`**?
6. Does a script belong under **`scripts/`** (or **`benchmarks/`** if it is a validation driver)?
7. Does documentation belong in **`docs/`** or an ADR?
8. Should this live at **repo root** at all?
9. Am I **reducing** complexity, or only **moving** it?

## Status

Living document for MetaCompressor. Refine when zones grow (e.g. nested packages under `metacompressor/`, new `config/` or `data/`). Update **`scripts/check_repo_layout.py`** and **ADR 0001** if machine-enforced Python paths change.

## Current repository audit (2026-05-04)

**Aligned with this policy**

- Flat **`metacompressor/*.py`** library; tests only under **`metacompressor/tests/`**.
- **`benchmarks/`** holds validation drivers; **`results/`** holds report artifacts.
- **`scripts/`** holds CI layout check; **`.github/workflows/`** runs layout check, **Ruff** (`ruff check`), **Black** (`black --check`), and **pytest**.
- **`docs/`** + **`docs/adr/`** for policy and ADRs; **`.cursor/`** for agent assets.

**Corrected**

- **`mc_test_output/`** and **`tmp_metacompressor_test/`** were previously tracked despite being scratch/fixture paths. They are listed in **`.gitignore`** and should remain **untracked**; use **`results/`** for committed reports or **`metacompressor/tests/`** + `tmp_path` for ephemeral test files.

**Optional later (not required now)**

- Introduce **`metacompressor/tests/fixtures/`** if small binary/text corpora need to live in-repo with clear ownership.
- Introduce **`config/`** or **`testdata/`** if non-Python config or shared datasets outgrow `docs/` + `results/`.
- Split very large modules (e.g. **`cli.py`**) only when boundaries are clear (see “When to split” above).
