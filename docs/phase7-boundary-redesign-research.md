# Phase 7 Boundary Redesign Research

Status: research plan (no runtime behavior changes)
Scope: revisit runtime reuse only at safer artifact boundaries.
Hard guardrail: do **not** resume chunk-local substitution.

## Why this phase exists

Prior runtime substitution experiments were fail-closed and default OFF, but still showed unstable byte parity at runtime candidate level (dominant `byte_parity_mismatch`). That makes chunk-local artifact reuse unsuitable for production under current contracts.

Reference evidence:

- `docs/differential-runtime-substitution-experimental-report.md`
- `docs/differential-runtime-substitution-boundary-finding.md`

## Required invariant

Any future reuse boundary must preserve:

- byte-identical parity against fresh builds on approved workloads
- lossless round-trip
- determinism across repeated runs
- explicit fail-closed fallback on uncertainty

No wire-format damage is allowed.

## Candidate boundaries to research

## 1) Msgpack-object-level boundary

**Idea:** Reuse higher semantic objects after deterministic assembly steps, not raw chunk artifacts.

**Potential advantage:**

- captures more archive-global structure than chunk bytes
- less sensitive to local chunk ordering drift

**Risk:**

- object dependency graph may still leak global ordering constraints

## 2) Assembly-state cache boundary

**Idea:** Cache full deterministic assembly state needed to replay identical payload construction.

**Potential advantage:**

- strongest chance of parity if full state is modeled correctly

**Risk:**

- state model may become large/complex
- invalidation logic can become brittle

## 3) Dependency-graph rebuild boundary

**Idea:** Recompute only affected assembly graph nodes and deterministically rebuild impacted regions.

**Potential advantage:**

- bounded rebuild work without pretending chunks are isolated

**Risk:**

- graph correctness and invalidation are hard
- hidden global dependencies can break parity

## 4) Higher-level reusable model objects

**Idea:** Reuse validated model outputs (template/group/column objects) while still serializing fresh final payload.

**Potential advantage:**

- safer than byte artifact reuse
- likely better aligned with existing warm-path intelligence

**Risk:**

- may provide speed gains smaller than hoped

## Evaluation harness (must pass)

Every candidate boundary must be evaluated with:

- byte parity gate (candidate bytes vs fresh bytes)
- deterministic repeatability gate
- correctness/lossless gate
- workload spread (structured, append-only, mixed, noisy)
- fail-closed diagnostics for each rejection reason

## GO / NO-GO rules

**NO-GO** unless all are true:

- parity pass rate meets target on approved workloads
- fallback rate is low enough for practical benefit
- no regressions in correctness/determinism
- no wire-format changes required (unless separately approved)

**GO** only for a narrowly bounded experimental path with default OFF and explicit rollback conditions.

## Recommendation now

- Keep runtime substitution paused.
- Continue extracting wins from safe layers (adoption UX, warm-path diagnostics, higher-level profiling/shaping research).
- Re-enter runtime reuse only after one boundary design shows credible parity evidence in this phase’s harness.
