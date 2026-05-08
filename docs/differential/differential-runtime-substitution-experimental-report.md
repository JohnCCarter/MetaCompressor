# Differential Runtime Substitution Experimental Report

Status: experimental research only
Mode: fail-closed, explicit flag-gated (`MC_ENABLE_PARTIAL_REUSE_RUNTIME=1`)
Default behavior: unchanged (`OFF`)

## Scope performed

This experiment implemented a runtime substitution candidate path inside differential orchestration with strict fail-closed fallback:

- reusable chunks attempt artifact-based substitution
- changed chunks rebuild from source
- deterministic merge replay in manifest order
- real decision metadata parity gate
- byte parity gate against fresh build
- noisy fail-closed gate
- always return `fresh_full_build` (no cache-return)

## Hard constraints check

- no cache-return: preserved
- no wire-format change: preserved
- default OFF: preserved
- frozen runtime files untouched: preserved
- full rebuild fallback available: preserved

## Evidence snapshot

From `results/differential/differential_parity_gate.json`:

- `verification_mode`: `partial_reuse_runtime_experimental`
- `simulation_only`: `false`
- `returned_archive_source`: `fresh_full_build`
- `real_decision_metadata_used`: `true`
- `byte_identical_parity_rate`: `1.0` (fresh output parity)
- `runtime_substitution_used_rate`: `0.45`
- `runtime_substitution_fail_reason_counts`: `{ "byte_parity_mismatch": 30 }`

From `results/differential/differential_partial_reuse_simulation.json`:

- runtime used rate by workload:
  - append-only logs: `0.45`
  - structured corpora: `0.90`
  - mixed binaries: `0.30`
  - noisy datasets: `0.00` (fail-closed as required)
- fallback rate by workload:
  - append-only logs: `0.55`
  - structured corpora: `0.10`
  - mixed binaries: `0.70`
  - noisy datasets: `1.00`

## Interpretation

The safety model works (fail-closed + fresh return + noisy disable), but runtime substitution candidate parity is not stable enough:

- fallback frequency is high on key workloads
- `byte_parity_mismatch` is a dominant runtime substitution failure reason
- deterministic production substitution cannot be justified yet

## GO / NO-GO recommendation

Recommendation: **NO-GO for productionization** at this point.

Rationale:

- runtime substitution candidate does not meet parity stability bar
- fallback rates are too high for reliable economic activation
- noisy behavior is correct (fail-closed), but target workload consistency is insufficient

## Next required work before reconsideration

1. tighten artifact validation and chunk mapping fidelity
2. reduce `byte_parity_mismatch` to near-zero on approved workloads
3. improve substitution used rate while keeping fail-closed behavior
4. rerun parity/benchmark evidence with same locked thresholds

Until then:

- keep runtime substitution experimental only
- keep default OFF
- keep `fresh_full_build` return contract
