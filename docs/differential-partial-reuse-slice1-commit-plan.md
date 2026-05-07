# Differential Partial Reuse — Slice 1 Commit Plan (A/B/C)

Status: planning only (no code in this step)
Scope lock: verification-first, flag-gated, default OFF, fresh-return only

Hard rules for all commits:

- Default OFF
- No cache-return
- No Phase 3 behavior
- No wire-format change
- Frozen files remain untouched

Frozen files (must not change in A/B/C):

- `metacompressor/corpus_template.py`
- `metacompressor/compressor.py`
- `metacompressor/decompressor.py`
- `metacompressor/container.py`
- `metacompressor/cli.py`
- Any `.mc1` serialization/deserialization semantics

---

## Commit A — Schema + Persistence Plumbing

Objective:

- Introduce per-chunk artifact metadata schema and persistence helpers.
- Add strict validation utilities and schema-focused tests.
- No orchestration behavior change.

### Files touched

- `metacompressor/differential/persistence.py`
- `metacompressor/differential/core.py` (only if shared schema structs/helpers are needed)
- `metacompressor/tests/test_differential_persistence.py`
- Optional new focused test: `metacompressor/tests/test_differential_partial_reuse_gate.py` (schema section only)

### Deliverables

- Schema fields present and validated:
  - `schema_version`
  - `encoder_version`
  - `chunk_hash`
  - `size_bytes`
  - `chunk_size`
  - `use_delta`
  - `profile_flags`
  - `path_hint`
  - `artifact_hash`
- Save/load helpers for per-chunk metadata.
- Validation helper returning deterministic pass/fail + reason.

### Tests to run

- `python -m pytest metacompressor/tests/test_differential_persistence.py -v`
- `python -m pytest metacompressor/tests/test_differential_partial_reuse_gate.py -k schema -v` (if created)
- `ruff check metacompressor`
- `black --check metacompressor`

### Expected evidence

- Missing/malformed required field fails validation with explicit reason.
- Version/config mismatch fails validation deterministically.
- Serialization round-trip for metadata passes.
- No change to differential orchestrator return behavior.

### Rollback boundary

- Revert Commit A only to remove schema/persistence plumbing.
- No downstream behavior rollback required because orchestration is unchanged.

---

## Commit B — Verification-Only Orchestration

Objective:

- Add flag-gated verification path in differential orchestration.
- Build simulated selective candidate (reuse/rebuild sets).
- Add deterministic merge gate and fallback reason reporting.
- Always return fresh archive.

### Files touched

- `metacompressor/differential/orchestrator.py`
- `metacompressor/tests/test_differential_orchestrator.py`
- `metacompressor/tests/test_differential_partial_reuse_gate.py` (orchestration/gate cases)

### Deliverables

- Gate flag respected: `MC_ENABLE_PARTIAL_REUSE_EXPERIMENT=1`.
- Flag OFF => exact baseline behavior.
- Flag ON => verification path runs, but returned archive source remains fresh build.
- Simulated selective candidate built from manifest diff + receipt/artifact validation.
- Deterministic merge validation in manifest order.
- Fallback reasons emitted (reason buckets only, no cache-return path).

### Tests to run

- `python -m pytest metacompressor/tests/test_differential_orchestrator.py -v`
- `python -m pytest metacompressor/tests/test_differential_partial_reuse_gate.py -k "flag or merge or fallback" -v`
- `python -m pytest metacompressor/tests -q`
- `ruff check metacompressor`
- `black --check metacompressor`

### Expected evidence

- Flag OFF invariance confirmed (outputs unchanged).
- Flag ON executes verification logic with recorded gate outcomes.
- Deterministic merge violations fail closed and are counted.
- Returned archive explicitly verified as fresh full build in all cases.

### Rollback boundary

- Revert Commit B to disable orchestration path entirely.
- Commit A can remain safely (schema/persistence only).

---

## Commit C — Gates + Benchmarks

Objective:

- Add and enforce parity gates in verification path:
  - byte-identical parity gate
  - strategy/encoding parity gate
  - noisy fail-closed gate
- Ensure report completeness.
- Align parity/simulation harness reporting with Slice 1 fields.

### Files touched

- `metacompressor/differential/orchestrator.py`
- `benchmarks/differential_parity_gate.py`
- `benchmarks/differential_partial_reuse_simulation.py` (alignment only)
- `metacompressor/tests/test_differential_orchestrator.py`
- `metacompressor/tests/test_differential_parity_gate_harness.py`
- `metacompressor/tests/test_differential_partial_reuse_gate.py`

### Deliverables

- Byte-identical parity gate enforced; mismatch => fail-closed.
- Strategy/encoding parity gate enforced; mismatch => fail-closed.
- Noisy scenario gate enforced with explicit threshold result.
- Report completeness fields present:
  - `simulation_only`
  - `partial_reuse_experiment_enabled`
  - `verification_mode`
  - `returned_archive_source`
  - `byte_identical_parity_pass`
  - `strategy_encoding_match_pass`
  - `deterministic_merge_pass`
  - `noisy_fail_closed_pass`
  - `fallback_reason_counts`
  - `reuse_candidate_chunk_count`
  - `rebuild_candidate_chunk_count`
  - `gates_evaluated`
  - `gates_failed`
- Harness alignment maintained with fresh-return-only semantics.

### Tests to run

- `python -m pytest metacompressor/tests/test_differential_parity_gate_harness.py -v`
- `python -m pytest metacompressor/tests/test_differential_partial_reuse_gate.py -k "parity or strategy or noisy or report" -v`
- `python -m pytest metacompressor/tests/test_differential_orchestrator.py -v`
- `python -m pytest metacompressor/tests -q`
- `ruff check metacompressor benchmarks`
- `black --check metacompressor benchmarks`
- `python benchmarks/differential_parity_gate.py --run-count 20`
- `python benchmarks/differential_partial_reuse_simulation.py --run-count 20`

### Expected evidence

- Parity and strategy gates are deterministic and fail closed on mismatch.
- Noisy fail-closed policy reliably triggers in noisy scenarios.
- Fallback reason coverage is complete and auditable.
- Benchmark/harness outputs remain simulation/verification-only.
- Fresh archive return contract preserved.

### Rollback boundary

- Revert Commit C to remove new gates/reporting/harness alignment.
- Commit B continues to provide gated verification scaffolding.
- Commit A remains baseline metadata plumbing.

---

## Cross-commit acceptance map

After Commit A:

- Schema/persistence groundwork validated.

After Commit B:

- Verification path exists behind explicit flag, still fresh-return only.

After Commit C:

- Full Slice 1 gate set proven with reports and benchmark evidence.

Final Slice 1 pass criteria:

- Default OFF behavior unchanged.
- No cache-return introduced.
- No wire-format change introduced.
- Frozen files untouched.
- Verification-only path gated, deterministic, and auditable.
