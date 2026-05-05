# hybrid_row_columnar_v1 — implementation summary

## What shipped

- **Wire mode** `corpus_template_hybrid_row_columnar_v1` (`_MODE_HYBRID_ROW_COLUMNAR_V1`): same outer columnar `.mck` layout as v2 (`templates`, `files`, `template_blocks`, `raw_files`, `metadata.raw_lines`) with an optional per-block **`dense_rows`** representation instead of **`columns`** when that block’s canonical msgpack is strictly smaller (tie → columnar layout).
- **Adaptive** `adaptive="v2.2+hybrid"`: same predictor path as `v2.2`, builds hybrid when columnar v2 is built, and runs `_adaptive_v2_pick` with an extra pool entry `hybrid_row_columnar_v1`. **TAR+ZSTD-in-MCK** remains mandatory. **Tie-break** among equal final sizes: `row_template` (0) < `hybrid_row_columnar_v1` (1) < `columnar_encoding_v2` (2) < `raw_tar_zstd` (4).
- **Metadata** (under `metrics["predictive_v2"]["hybrid_row_columnar_v1"]` and duplicated `metrics["hybrid_row_columnar_v1"]` when hybrid mode is active): `eligible`, `eligibility_reason`, `structure_score`, `estimated_overhead_vs_columnar_v2_bytes`, `final_selected_mode`.

## Files touched

| Path | Role |
|------|------|
| `metacompressor/corpus_template.py` | Mode constants, hybrid finalize path, `v2.2+hybrid` compress branch, `_adaptive_v2_pick` hybrid pool + tie order, decompress `dense_rows` |
| `metacompressor/tests/test_adaptive_selection_v2.py` | Round-trip, determinism, eligibility, TAR guard, `_adaptive_v2_pick` unit tests, parametrize `v2.2+hybrid` |
| `scripts/benchmark_adaptive_hybrid_v1.py` | Benchmark driver v1 / v2.2 / v2.2+hybrid |
| `results/metacompressor_adaptive_hybrid_v1_benchmark.md` | Generated report |
| `results/metacompressor_hybrid_row_columnar_v1_implementation_summary.md` | This file |

## Benchmark (script datasets)

From `results/metacompressor_adaptive_hybrid_v1_benchmark.md`:

- **Win-rate vs TAR+ZSTD**: 80.0% for `v1`, `v2.2`, and `v2.2+hybrid` (unchanged on these five corpora).
- **Avg delta vs TAR+ZSTD**: −26.99% for all three (hybrid candidate is sometimes larger than pure columnar after zstd, so the same mode as v2.2 still wins).
- **Worst loss**: 1.82% for all three (meets “no worse than v2.2” on this suite).

On these datasets **hybrid did not become the selected mode**; it remains available for tie-breaking and for corpora where dense blocks shrink the final `.mck`. **Synthetic proof** that hybrid can win the pool vs both row and columnar is in `test_adaptive_v2_pick_hybrid_strictly_smaller_than_row_and_columnar` and the size-tie test.

## Regressions / risks

- **`v2.2+hybrid` encode cost**: one extra full pass matching columnar build when columnar v2 is built.
- **Tie-break change** (hybrid before columnar on **identical** `.mck` byte length) applies only when `hybrid_pack` is present; `v2.2` without hybrid is unchanged.
