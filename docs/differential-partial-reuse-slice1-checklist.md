# Differential Partial Reuse — Slice 1 Task Checklist (Pre-code)

Status: planning checklist only (no implementation in this step)
Scope lock: flag-gated verification path only, default OFF, no cache-return, no wire-format change

## 1) Implementation order

1. Confirm guard contract in orchestrator entry path:
   - experimental path runs only when `MC_ENABLE_PARTIAL_REUSE_EXPERIMENT=1`
   - otherwise current behavior is untouched.
2. Add per-chunk artifact metadata plumbing in persistence layer:
   - read/write metadata structure
   - strict schema validation helpers
   - advisory-only usage (no output substitution).
3. Add verification-only selective candidate builder:
   - compute reusable vs rebuild sets from manifest diff + receipts + artifact metadata validation
   - construct a simulated selective candidate in deterministic manifest order.
4. Add strategy/encoding parity gate:
   - compare verification candidate signature vs fresh full build signature
   - fail closed on mismatch.
5. Add byte-identical parity gate:
   - compare verification candidate bytes vs fresh archive bytes
   - fail closed on mismatch.
6. Ensure return contract:
   - always return fresh archive bytes in Slice 1, regardless of gate outcome.
7. Emit structured reporting fields:
   - gate outcomes + fallback reasons + verification mode flags.

## 2) Test order

1. **Baseline invariance (flag OFF)**
   - outputs and behavior equal pre-slice baseline.
2. **Flag ON verification path active**
   - selective candidate logic executes, but returned archive remains fresh.
3. **Schema validation tests**
   - missing field / malformed field / version mismatch => fallback.
4. **Deterministic merge tests**
   - manifest-order merge passes on normal case.
   - duplicate/order violation fails closed.
5. **Strategy/encoding parity tests**
   - mismatch path triggers fallback reason.
6. **Byte parity tests**
   - mismatch triggers fail-closed and explicit reason.
7. **Noisy fail-closed tests**
   - noisy scenario crosses threshold -> reuse disabled.
8. **Report completeness tests**
   - all required report fields and fallback counters exist.

## 3) Exact files to edit

- `metacompressor/differential/orchestrator.py`
- `metacompressor/differential/persistence.py`
- `metacompressor/differential/core.py` (only if schema/helper support is required)
- `metacompressor/tests/test_differential_orchestrator.py`
- `metacompressor/tests/test_differential_persistence.py`
- `metacompressor/tests/test_differential_parity_gate_harness.py`
- `metacompressor/tests/test_differential_partial_reuse_gate.py` (new focused tests if needed)
- `benchmarks/differential_parity_gate.py` (report alignment only)
- `benchmarks/differential_partial_reuse_simulation.py` (report alignment only)

## 4) Exact files frozen

- `metacompressor/corpus_template.py`
- `metacompressor/compressor.py`
- `metacompressor/decompressor.py`
- `metacompressor/container.py`
- `metacompressor/cli.py`
- Any `.mc1` format serialization/deserialization path

## 5) Required report fields

Required top-level fields for Slice 1 verification reporting:

- `simulation_only` (must remain true for this slice)
- `partial_reuse_experiment_enabled`
- `verification_mode`
- `returned_archive_source` (must be `fresh_full_build`)
- `byte_identical_parity_pass`
- `strategy_encoding_real_match_pass`
- `deterministic_merge_pass`
- `noisy_fail_closed_pass`
- `real_decision_metadata_used`
- `fallback_reason_counts`
- `reuse_candidate_chunk_count`
- `rebuild_candidate_chunk_count`
- `gates_evaluated`
- `gates_failed`

Recommended supplemental fields:

- `artifact_schema_validation_pass`
- `artifact_schema_version`
- `config_match_pass`
- `receipt_validation_pass`
- `artifact_validation_pass`

## 6) Fallback reason coverage

Slice 1 must explicitly cover and count:

- `config_mismatch`
- `manifest_inconsistent`
- `receipt_missing_or_stale`
- `artifact_missing`
- `artifact_hash_mismatch`
- `real_decision_metadata_missing`
- `real_decision_metadata_unavailable`
- `strategy_encoding_real_mismatch`
- `byte_parity_mismatch`
- `deterministic_merge_violation`
- `noisy_fail_closed`

Checklist pass condition:

- every reason above has at least one test case
- every triggered reason increments `fallback_reason_counts`
- fallback always preserves fresh return behavior

## 7) Final validation commands

Run from repo root after Slice 1 code is implemented:

- `python -m pytest metacompressor/tests/test_differential_persistence.py -v`
- `python -m pytest metacompressor/tests/test_differential_orchestrator.py -v`
- `python -m pytest metacompressor/tests/test_differential_parity_gate_harness.py -v`
- `python -m pytest metacompressor/tests/test_differential_partial_reuse_gate.py -v` (if created)
- `python -m pytest metacompressor/tests -q`
- `ruff check metacompressor benchmarks`
- `black --check metacompressor benchmarks`
- `python benchmarks/differential_parity_gate.py --run-count 20`
- `python benchmarks/differential_partial_reuse_simulation.py --run-count 20`

Acceptance summary for Slice 1:

- default OFF behavior unchanged
- verification path works only behind explicit flag
- per-chunk artifact metadata plumbing validated
- selective candidate is simulated and gated
- fresh archive always returned
- no cache-return, no Phase 3 behavior, no wire-format change
