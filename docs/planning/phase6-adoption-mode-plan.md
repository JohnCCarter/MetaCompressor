# Phase 6 Adoption-Mode Plan

Status: planning (docs-only)
Scope: usability and operator trust improvements only.
Non-goals: runtime substitution, wire-format changes, risky architecture changes.

## Goal

Make MetaCompressor usable for real users and companies by default, not only benchmark demonstrations.

## Adoption principles

- Fast and safe by default.
- Predictable behavior under uncertain workloads.
- Explain every major decision in human terms.
- Prefer explicit fallback over ambiguous "maybe faster" behavior.
- Keep lossless and deterministic guarantees visible in UX.

## 1) Fast default mode

### Target outcome

`mc` should run with a default mode that prioritizes practical throughput and safe selection without requiring expert flags.

### Plan

- Define one default profile (for example: `--mode fast-safe`) that:
  - keeps current safe path selection logic,
  - enables warm-path reuse where already validated,
  - avoids experimental shaping or substitution paths unless explicitly enabled.
- Keep advanced modes available, but clearly mark them as advanced/experimental.

### Success criteria

- New users can run `mc compress <dir>` without tuning.
- Default behavior is stable across repeated runs.

## 2) Clear fallback messaging

### Target outcome

Users should always know when fallback happened and why.

### Plan

- Standardize fallback reason labels in CLI/report output:
  - `raw_tar_zstd_threshold_exceeded`
  - `low_structure`
  - `no_templates`
  - `binary`
  - `safety_gate_disabled_path`
- Ensure one top-level fallback summary line plus per-reason counters.
- Distinguish "safe fallback" from "error".

### Success criteria

- Every fallback run has an explicit reason string and count.
- No silent fallback behavior.

## 3) Explainable selected path

### Target outcome

Users should understand why MC chose a path.

### Plan

- Show selected mode with short rationale:
  - `selected_mode`
  - `decision_reason`
  - key confidence or skip indicators (`analysis_skip_used`, `receipt_valid`, etc.).
- Keep wording concise and deterministic.

### Success criteria

- User can answer: "Why did MC choose this path?" from one command output.

## 4) Ratio/speed summary

### Target outcome

Users should see net value quickly.

### Plan

- Standard output block with:
  - input size
  - output size
  - ratio
  - delta vs TAR+ZSTD baseline
  - encode/decode time
- Add a one-line verdict:
  - `strong win`, `win`, `near tie`, `fallback recommended`.

### Success criteria

- Summary visible without reading raw JSON.
- Same fields available in machine-readable report.

## 5) Memory reporting

### Target outcome

Operators should be able to predict memory impact.

### Plan

- Surface peak memory in CLI and report for selected path.
- Include optional per-stage memory estimates where already available.
- Document expected memory behavior for small/medium/large corpora.

### Success criteria

- Memory numbers present in default report output.
- No hidden high-memory mode in default behavior.

## 6) Recommended workload guidance

### Target outcome

Users should know where MC is likely worth using.

### Plan

- Add docs section with practical guidance:
  - strong fit: structured logs, JSON/NDJSON, repeated templates, many small files
  - weaker fit: high-entropy/noisy corpora, already compressed/random bytes
- Link to Phase 5 benchmark summary as evidence.

### Success criteria

- User can self-classify workload before deployment.

## 7) CLI/report UX

### Target outcome

CLI should be clear in both human and automation contexts.

### Plan

- Keep human-readable summary as default.
- Add/maintain machine-readable output mode for CI (`--json`).
- Normalize field names between CLI summary and JSON report.
- Ensure non-zero exit codes only for true failures, not safe fallbacks.

### Success criteria

- CI and human users consume same core decision signals.

## 8) Safe defaults

### Target outcome

Defaults should prevent risky behavior automatically.

### Plan

- Keep experimental features default OFF.
- Keep fail-closed behavior for uncertain warm-path metadata.
- Keep deterministic path ordering and stable report semantics.
- Preserve existing no-runtime-substitution stance.

### Success criteria

- Default path remains conservative and reproducible.

## 9) Refuse/fallback policy (what MC should not force)

### Target outcome

MC should refuse unsafe paths and fallback predictably.

### Plan

- Explicitly refuse/disable path upgrades when:
  - receipt/manifest integrity fails,
  - confidence is below threshold,
  - required metadata is missing/mismatched,
  - parity-critical gates are unmet.
- Fallback to safe path or TAR+ZSTD wrapper depending on policy threshold.

### Success criteria

- Unsafe paths never execute silently.
- All refusal/fallback events are auditable in reports.

## Delivery sequence (low risk)

1. CLI/report wording normalization (no behavioral change).
2. Fallback reason taxonomy and summary block unification.
3. Fast default profile definition with existing safe logic only.
4. Workload guidance docs + benchmark link integration.
5. Memory and ratio/speed summary polish for adoption UX.

## Exit criteria for Phase 6

- Users can run MC with default settings and clearly see:
  - selected path
  - ratio and size delta vs baseline
  - speed summary
  - memory summary
  - fallback reason (if any)
  - whether MC was net beneficial
- No runtime substitution introduced.
- No wire-format changes introduced.
