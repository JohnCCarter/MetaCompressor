# Differential Partial Reuse — Implementation Packet (Slice 1)

Status: implementation packet only (no behavior change in this step)
Mode: verification-first, experimental, flag-gated
Default: OFF

## 1. Objective of this packet

Define one bounded implementation slice for real per-chunk artifact cache plumbing,
without enabling production behavior.

Hard constraints for Slice 1:

- Explicit flag only (`default OFF`)
- Verification mode first
- No cache-return
- No wire-format change
- Fail closed on any ambiguity

## 2. Bounded Slice 1 (what is in scope)

Slice 1 introduces a guarded experimental path that can:

1. Build/load per-chunk artifact metadata in cache storage.
2. Attempt simulated selective reuse decisions under strict validation.
3. Assemble a verification candidate and compare against fresh full build bytes.
4. Always return fresh full build output in this slice (no substitution).

Out of scope for Slice 1:

- Returning cached final archive
- Enabling default-on behavior
- Any `.mc1` container or wire-format semantic change
- Heuristic tuning for production promotion

## 3. Flag contract

Primary gate:

- `MC_ENABLE_PARTIAL_REUSE_EXPERIMENT=1` to enable experimental verification path.

Required mode guard:

- If flag not set: existing behavior only.
- If flag set: still verification-first, no cache-return.

Recommended reporting flag:

- Reuse existing differential reporting path to emit diagnostics.

## 4. Files allowed to edit

Only the following files are allowed for Slice 1:

- `metacompressor/differential/orchestrator.py`
- `metacompressor/differential/persistence.py`
- `metacompressor/differential/core.py` (only if schema helpers require it)
- `benchmarks/differential/differential_parity_gate.py`
- `benchmarks/differential/differential_partial_reuse_simulation.py` (reporting alignment only)
- `metacompressor/tests/differential/test_differential_orchestrator.py`
- `metacompressor/tests/differential/test_differential_persistence.py`
- `metacompressor/tests/differential/test_differential_parity_gate_harness.py`
- Optional new focused test: `metacompressor/tests/differential/test_differential_partial_reuse_gate.py`
- `./differential-partial-reuse-design.md` (if clarifications are needed)

## 5. Files frozen for Slice 1

Do not edit in this slice:

- `metacompressor/corpus_template.py`
- `metacompressor/compressor.py`
- `metacompressor/decompressor.py`
- `metacompressor/container.py`
- `metacompressor/cli.py`
- Any file that changes `.mc1` write/read semantics

## 6. Artifact schema (required fields)

Per-chunk artifact metadata must include:

- `schema_version`
- `encoder_version`
- `chunk_hash`
- `size_bytes`
- `chunk_size`
- `use_delta`
- `profile_flags`
- `path_hint`
- `artifact_hash`

Schema rules:

- Missing or malformed required field => fallback for that chunk.
- Version/config mismatch => fallback for that chunk or full fail-closed run.
- Schema must be deterministic and JSON-serializable.

## 7. Validation gates (must pass in runtime flow)

Gate order (verification path):

1. Config gate: `chunk_size`, `use_delta`, encoder version must match.
2. Manifest gate: old/new manifest integrity and deterministic ordering validated.
3. Receipt gate: chunk receipt exists and matches hash+size.
4. Artifact gate: artifact exists and `artifact_hash` validates.
5. Strategy gate: strategy/encoding signature must match fresh decision.
6. Byte parity gate: assembled verification bytes must equal fresh full bytes.

If any gate fails:

- mark explicit fallback reason
- disable reuse for affected chunk/run
- keep fresh full build as returned output

## 8. Byte-identical parity requirement

Non-negotiable contract:

- Verification candidate bytes must be exactly equal to fresh full build bytes.
- Lossless-only equivalence is insufficient.

Required assertion:

- `verification_candidate_bytes == fresh_archive_bytes`

Mismatch behavior:

- full fail-closed for that run
- increment parity mismatch fallback reason
- return fresh full build bytes

## 9. Fallback rules

Fallback must be explicit and auditable. Required reason buckets:

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

Policy:

- Any ambiguous state => fail closed.
- Noisy/high-entropy scenario crossing threshold => fail closed.
- Slice 1 never substitutes output with cached/partial result.

## 10. Required tests for Slice 1

Minimum required tests before merge:

1. **Flag-off invariance**
   - Experimental path disabled by default.
   - Outputs/metrics remain current baseline.

2. **Flag-on verification-only**
   - Experimental logic executes.
   - Returned archive still fresh full build.

3. **Schema validation**
   - Missing required fields triggers fallback.
   - Version/config mismatch triggers fallback.

4. **Deterministic merge checks**
   - Merge order strictly follows new manifest order.
   - Duplicate/out-of-order conditions trigger fail-closed.

5. **Strategy/encoding parity**
   - Mismatch triggers fallback reason and no reuse acceptance.

6. **Byte parity gate**
   - Any mismatch triggers `byte_parity_mismatch` and fail-closed.

7. **Noisy fail-closed**
   - Noisy scenario triggers fail-closed policy as expected.

8. **Reporting integrity**
   - Report includes gate outcomes + fallback reason counts.

## 13. Slice 1 stabilization status (current)

Current implementation status for this packet:

- Verification-only orchestration is in place and remains flag-gated.
- Real compressor decision metadata is used in gates (`real_decision_metadata_used`).
- `fresh_full_build` remains the only returned archive source.
- Cache-return remains disabled/not implemented.
- Wire-format remains unchanged.

## 11. Rollback plan

Immediate rollback path:

1. Disable via env flag (`MC_ENABLE_PARTIAL_REUSE_EXPERIMENT` unset/0).
2. If needed, remove experimental branch logic from orchestrator in one revert commit.
3. Preserve reports/tests for postmortem evidence.

Rollback triggers:

- Any nondeterminism in repeated runs
- Any byte-parity failure in verification suite
- Any unexplained regression in verification benchmarks
- Any accidental behavior outside allowed file boundary

## 12. Exit criteria for Slice 1 completion

Slice 1 is complete only when all are true:

- Default OFF behavior unchanged
- Verification-only path implemented behind explicit flag
- Runtime gates + fallback reasons fully emitted
- Required tests pass
- No wire-format or cache-return behavior introduced
