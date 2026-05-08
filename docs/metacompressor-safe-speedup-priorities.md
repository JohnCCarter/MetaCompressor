# MetaCompressor Safe Speedup Priorities

Status: planning note (no runtime code changes in this pass)

Constraints applied:

- no chunk-local runtime artifact substitution continuation
- no cache-return
- no wire-format changes

## Ranking (best next options first)

1. **Analysis-skip hardening/expansion**
2. **Receipt/manifest warm-path**
3. **Decision reuse**
4. **Template extraction overhead**
5. **ZSTD-affine shaping (report/advisory only unless strongly bounded)**

Ranking basis:

- speedup potential
- implementation safety
- likelihood of near-term success

---

## 1) Analysis-skip

- **Current evidence:** Existing Mycelium-lite evidence already shows strong time-saving potential in differential/quick paths, while preserving fail-closed behavior and `fresh_full_build` return.
- **Expected speed impact:** High in repeat/warm workloads (especially unchanged and append-only cases).
- **Implementation risk:** Low-to-medium (mostly gating, confidence checks, and skip eligibility logic).
- **Files likely touched:** `benchmarks/acceptance_hardening.py`, `metacompressor/differential/orchestrator.py`, related quick-mode reporting/tests.
- **Tests needed:** differential gate tests, quick-mode regression tests, determinism + lossless checks, report-schema checks.
- **Recommended next action:** Tighten and expand safe skip eligibility (only when receipts/manifests/decision metadata are consistent), with richer miss attribution.

## 2) Receipt/manifest warm-path

- **Current evidence:** Receipts/manifests are stable, already persisted, and central to safe differential orchestration.
- **Expected speed impact:** Medium-high by reducing re-validation/re-analysis cost on warm runs.
- **Implementation risk:** Low (state reuse + fail-closed checks; no wire-format coupling required).
- **Files likely touched:** `metacompressor/differential/core.py`, `metacompressor/differential/persistence.py`, `metacompressor/differential/orchestrator.py`, harnesses in `benchmarks/`.
- **Tests needed:** persistence roundtrip, schema compatibility, stale/missing/corrupt state fail-closed tests.
- **Recommended next action:** Add explicit warm-path fast checks and short-circuit validation pipeline when manifest+receipt integrity fully matches.

## 3) Decision reuse

- **Current evidence:** `real_decision_metadata_used` is already integrated and deduplicated per run; gates are in place.
- **Expected speed impact:** Medium (avoids repeated expensive decision feature work and unnecessary candidate exploration).
- **Implementation risk:** Low-to-medium (must avoid stale decision reuse and keep fail-closed semantics).
- **Files likely touched:** `metacompressor/differential/orchestrator.py`, decision report generation in benchmark scripts, decision-related tests.
- **Tests needed:** stale decision metadata invalidation, match/mismatch coverage, determinism checks.
- **Recommended next action:** Promote decision metadata reuse to first-class warm-path signal for safe “no-rescan-analysis” decisions where confidence and config match.

## 4) Template extraction overhead

- **Current evidence:** Prior instrumentation identified template extraction and transform call decomposition as meaningful cost centers; small-corpus inline path already helped.
- **Expected speed impact:** Medium (broadly beneficial, especially on medium corpora with many files).
- **Implementation risk:** Medium (touches core corpus-template execution path and concurrency behavior).
- **Files likely touched:** `metacompressor/corpus_template.py`, performance benchmarks in `benchmarks/acceptance_hardening.py`, tests in `metacompressor/tests/test_acceptance_hardening.py`.
- **Tests needed:** timing breakdown consistency, lossless roundtrip, deterministic output checks, Windows process-mode stability checks.
- **Recommended next action:** Target unexplained extraction overhead with bounded micro-optimizations that do not alter template semantics.

### Tokenization micro-opt note (implemented)

- **Optimization:** `_normalize_text_part` now uses `lru_cache(maxsize=131072)`.
- **Reason:** Template extraction repeatedly normalizes recurring text segments across lines/files; caching avoids repeated normalization work for identical inputs.
- **Measured impact (100MB workload):**
  - `tokenization_time_ms`: `39,352 -> 33,403` (~15.1% faster)
  - `template_extract_time_ms`: `51,306 -> 41,241` (~19.6% faster)
- **Output invariance:** `output_size_bytes` unchanged (`11,819,419 -> 11,819,419`).
- **Validation status:** `pytest` `317 passed, 8 skipped`; `ruff` and `black --check` passed.
- **Risk note:** cache memory growth is bounded by `maxsize=131072` (eviction after bound is reached).

### Hotpath optimization history

See `docs/template-hotpath-optimization-summary.md` for the consolidated history of accepted template/tokenization hotpath wins, rejected candidates that were reverted, and current status.

- Accepted hotpath micro-opts produced practical speedups while preserving output/mode/correctness/determinism gates.
- Rejected candidates were explicitly reverted when median gates failed.
- Current recommendation: pause further tokenization micro-opts until new profiler evidence appears; shift focus toward ZSTD-affine shaping or higher-level build-path profiling.

## 5) ZSTD-affine shaping

- **Current evidence:** Shaping has been treated as experimental/report-only. Recent runtime parity analysis indicates global assembly sensitivity, so compression-shape perturbations must be very controlled.
- **Expected speed impact:** Low-to-medium and workload-dependent.
- **Implementation risk:** Medium-to-high (easy to introduce unstable or non-obvious effects; closer to archive-global behavior).
- **Files likely touched:** primarily benchmark/reporting surfaces first; runtime touches would require extra scrutiny.
- **Tests needed:** strict parity/determinism verification, ratio-vs-time tradeoff evidence, regression guardrails.
- **Recommended next action:** Keep shaping advisory/diagnostic until higher-confidence wins are exhausted.

---

## Single recommended next implementation target

**Pick: Analysis-skip (safe eligibility expansion + warm-path integration).**

Why this one:

- highest near-term speedup potential under current architecture
- strongest safety profile with existing fail-closed controls
- minimal coupling to wire format and no dependency on runtime artifact substitution

## Explicit non-goal in next slice

Do not resume chunk-local runtime artifact substitution until boundary redesign is completed and a new parity contract is proven.
