# Differential Slice 2 Candidate (Research/Architecture Pause)

Status: candidate plan only (no implementation)
Decision state: pre-go/no-go planning
Scope intent: prepare decision-quality criteria for possible runtime artifact substitution in a future slice

## 1. Objective

Define a strict, evidence-driven candidate plan for the next differential step after Slice 1, without implementing runtime artifact substitution.

## 2. Background from Slice 1

Slice 1 established a verification-only Mycelium-lite layer:

- differential orchestration with fail-closed behavior
- per-chunk metadata plumbing
- deterministic merge/parity/noisy gates
- real decision metadata gate (`real_decision_metadata_used`)
- simulation economics harness and parity harness

Runtime behavior constraints preserved:

- default OFF (`MC_ENABLE_PARTIAL_REUSE_EXPERIMENT=1` required)
- no cache-return
- no wire-format change
- `fresh_full_build` remains returned output

## 3. Current Evidence Summary

Based on current reports:

- verification mode remains simulation-only
- `real_decision_metadata_used: true`
- returned archive source remains `fresh_full_build`
- parity gate remains green (`byte_identical_parity_rate = 1.0`)
- deterministic merge and noisy fail-closed remain pass
- simulated economic signal remains strong:
  - append-only logs: ~94.19%
  - structured corpora: ~90.0%
  - mixed binaries: ~94.55%
  - noisy datasets: ~0.0%

## 4. Scope (candidate only)

Slice 2 candidate scope, if approved later:

- design and test a runtime substitution path behind explicit flag
- keep fail-closed gating mandatory
- prove byte-identical parity under real substitution logic
- keep default OFF while gathering runtime evidence

## 5. Non-goals (explicit)

- no implementation in this document
- no cache-return
- no Phase 3 activation
- no wire-format change
- no default-ON behavior
- no frozen runtime file edits unless explicitly approved

## 6. Allowed Files (for future Slice 2 implementation candidate)

- `metacompressor/differential/orchestrator.py`
- `metacompressor/differential/persistence.py`
- `metacompressor/differential/core.py` (if schema/index helpers are needed)
- `metacompressor/tests/differential/test_differential_orchestrator.py`
- `metacompressor/tests/differential/test_differential_partial_reuse_gate.py`
- `metacompressor/tests/differential/test_differential_persistence.py`
- `benchmarks/differential/differential_parity_gate.py`
- `benchmarks/differential/differential_partial_reuse_simulation.py`
- new focused differential tests only under `metacompressor/tests/differential/`
- docs under `docs/differential/`

## 7. Frozen Files

- `metacompressor/corpus_template.py`
- `metacompressor/compressor.py`
- `metacompressor/decompressor.py`
- `metacompressor/container.py`
- `metacompressor/cli.py`
- any path that changes `.mc1` read/write semantics

## 8. Runtime Substitution Risks

1. **Behavior drift risk**
   - substituted artifact path may diverge from fresh build decision flow.
2. **Parity risk**
   - runtime substitution can pass lossless checks but fail byte-identical checks.
3. **Gate ordering risk**
   - incorrect gate order can permit substitution before full validation.
4. **Hidden state risk**
   - stale cache/meta interactions can produce non-obvious divergence.

## 9. Artifact Integrity Risks

- missing artifact files
- artifact hash mismatch
- stale/missing receipt coverage
- schema/version mismatch across cache lifecycle
- path/identity mismatch between manifest and artifact index

All must fail closed with explicit reason attribution.

## 10. Deterministic Merge Risks

- non-manifest ordering dependencies
- duplicate chunk IDs in merge assembly
- platform/process-order dependent behavior
- unstable tie-breaks in mixed reuse/rebuild sets

Mitigation requirement: deterministic assembly proven via repeated-run equality tests.

## 11. Noisy / Fail-Closed Risks

- false reuse under high mutation/noise
- threshold drift causing accidental reuse in noisy workloads
- partial scans masking widespread churn

Mitigation requirement: noisy scenarios remain fail-closed with explicit bucket attribution.

## 12. Required Tests Before Any Slice 2 Implementation

1. flag OFF baseline invariance
2. flag ON verification path + fresh return unchanged
3. substitution-path parity tests (byte-identical vs fresh build)
4. corrupted/missing artifact fail-closed tests
5. stale receipt policy tests (chunk-level + high-ratio fail-closed)
6. deterministic merge replay tests (multi-run stability)
7. cross-scenario tests: append-only, structured small-change, mixed binaries, noisy
8. fallback reason completeness assertions

## 13. Required Benchmark Evidence

Before enabling any runtime substitution mode:

- parity remains 1.0 on eligible scenarios under substitution candidate
- noisy workloads remain fail-closed
- default OFF path runtime/ratio behavior unchanged
- no worst-case regressions beyond agreed limits
- repeated runs show deterministic outputs

## 14. Economic Activation Threshold (proposed)

Runtime artifact substitution may be considered only if all are true:

- byte-identical parity remains `1.0` on target workloads
- noisy datasets remain fail-closed
- default OFF path remains unchanged
- speedup signal is consistently above lock threshold (proposed: `>= 25%` on target workloads)
- worst-case regression remains under lock threshold (proposed: `<= 5%`)

These values are proposal defaults and require explicit acceptance before activation.

## 15. Go / No-Go Criteria

Go candidate only when:

- all required tests pass
- benchmark evidence satisfies thresholds
- fail-closed attribution remains complete
- no wire-format drift detected
- no deterministic regressions observed

No-Go if any condition above fails.

## 16. Rollback Plan

If future Slice 2 experiments regress:

1. disable via flag (default OFF remains control plane)
2. revert substitution-path commits only
3. preserve diagnostics and reports for postmortem
4. keep Slice 1 verification layer as stable baseline

## 17. Slice 2 Candidate Stance (mandatory)

- candidate plan only
- no implementation yet
- no cache-return
- no Phase 3
- no wire-format change
- default OFF
- `fresh_full_build` remains required until real substitution parity is proven
