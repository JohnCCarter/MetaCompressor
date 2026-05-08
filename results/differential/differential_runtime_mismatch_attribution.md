# Differential Runtime Mismatch Attribution

Generated from:

- `results/differential/differential_parity_gate.json`
- `results/differential/differential_partial_reuse_simulation.json`

## 1) Mismatch attribution report

- `runtime_substitution_fail_reason_counts`: `{"byte_parity_mismatch": 30}`
- `mismatch_stage_counts`: `{"msgpack_structure": 30, "none": 30}`
- `mismatch_first_byte_offset`: consistently `10` in mismatch cases
- `container_metadata_equal_rate`: `1.0`
- `payload_order_equal_rate`: `1.0`
- `msgpack_structure_equal_rate`: `0.5`
- `zstd_frame_equal_rate`: `0.5`
- `size_delta_avg`: `-9.9` bytes (candidate smaller on average)

Interpretation:

- The dominant mismatch source is post-header payload divergence (`offset=10`) in the compressed payload area.
- Header/container metadata is stable (`container_metadata_equal=true`), so the mismatch is not caused by magic/version/chunk_size header reconstruction.
- The strongest signal is payload/msgpack structural divergence, with a secondary framing-level divergence (`zstd_frame_equal_rate=0.5`).

## 2) Per-workload mismatch breakdown

- append-only logs:
  - `runtime_mismatch_stage_counts`: `{"msgpack_structure": 10, "none": 10}`
  - `runtime_substitution_used_rate`: `0.45`
  - `runtime_substitution_fallback_rate`: `0.55`
- structured corpora:
  - `runtime_mismatch_stage_counts`: `{"msgpack_structure": 1, "none": 19}`
  - `runtime_substitution_used_rate`: `0.9`
  - `runtime_substitution_fallback_rate`: `0.1`
- mixed binaries:
  - `runtime_mismatch_stage_counts`: `{"msgpack_structure": 12, "none": 7, "payload_order": 1}`
  - `runtime_substitution_used_rate`: `0.3`
  - `runtime_substitution_fallback_rate`: `0.7`
- noisy datasets:
  - `runtime_mismatch_stage_counts`: `{"msgpack_structure": 19, "none": 1}`
  - `runtime_substitution_used_rate`: `0.0` (fail-closed)
  - `runtime_substitution_fallback_rate`: `1.0`

## 3) Per-stage candidate-vs-fresh diff metadata

Recorded in runtime reports per run and aggregated per workload:

- `mismatch_stage`
- `mismatch_first_byte_offset`
- `candidate_size`, `fresh_size`, `size_delta`
- `artifact_count_reused`, `artifact_count_rebuilt`
- `container_metadata_equal`
- `payload_order_equal`
- `zstd_frame_equal`
- `msgpack_structure_equal`
- `suspected_global_dependency`

Current aggregate signal:

- container metadata stable
- occasional payload-order drift (mixed binaries)
- repeated msgpack structure differences
- zstd frame divergence follows structural divergence

## 4) Recommendation

Recommendation: **NOT FIXABLE with confidence in current artifact boundary** (no wire-format change applied).

Why:

- Mismatches cluster in payload structural differences despite stable container metadata, indicating archive-global assembly dependencies beyond raw chunk byte reuse.
- This suggests chunk-local artifact substitution at current boundary is insufficient for byte-identical archive reproduction across all workloads.
- A robust fix likely requires a different substitution artifact boundary and/or additional globally normalized assembly state.

Wire-format stance:

- No wire-format change implemented.
- If future remediation requires changing on-disk assembly semantics, that must be treated as a separate explicit format decision (out of this experiment).

GO/NO-GO:

- **NO-GO remains** until parity instability is removed under the existing safety constraints.
