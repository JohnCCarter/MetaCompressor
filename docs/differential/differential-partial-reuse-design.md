# Differential Partial Reuse Plan (Design Only)

Status: proposal (no implementation)
Scope: verification findings -> implementation blueprint
Non-goals: no cache-return in this phase, no wire-format change, no compression behavior change

## 1. Why this exists

Current verification results show:

- `archives_equal_given_cache_hit_rate` is high (safety on full-hit looks strong).
- Mutating scenarios still have low all-or-nothing hit-rate.
- Append-only and mixed small-change scenarios often have many reusable chunks even when full hit is false.

Inference: we should design a **partial reuse path** (reuse unchanged chunks, rebuild only changed chunks), but keep it as a plan until gated tests are defined and passed.

## 2. Target behavior (future implementation)

Given old manifest + receipts + cached per-chunk artifacts and a new manifest:

- **Reusable chunks**: chunks proven unchanged by chunk id + hash + size.
- **Changed chunks**: chunk ids present in both manifests but hash and/or size differ.
- **New chunks**: chunk ids present only in new manifest.
- **Deleted chunks**: chunk ids present only in old manifest.

Future encode flow (conceptual):

1. Build new manifest.
2. Diff manifests + validate receipts/artifacts.
3. Reuse artifact bytes for reusable chunks.
4. Rebuild artifacts for changed/new chunks only.
5. Merge reused + rebuilt chunks in deterministic new-manifest order.
6. Produce fresh final archive bytes (same format as today).

No cached final archive substitution in this plan unless a later explicit Phase 3 approval.

## 3. Chunk boundary stability assumptions

Partial reuse only helps if chunk boundaries are stable enough.

- Keep current chunking contract and deterministic chunk ids.
- Treat boundary drift as normal mutation (falls into changed/new/deleted).
- For append-only logs, expect front/middle chunks to remain stable and tail chunks to mutate.
- If boundary instability is high, fallback to full rebuild.

## 4. Cached artifact model (future)

Store sidecar cache in a dedicated cache directory (not user data):

- `manifest.json` (already present)
- `receipts.json` (already present)
- `cache_meta.json` (already present)
- **new**: per-chunk artifact store, e.g.:
  - `chunks/<chunk_id>.artifact`
  - optional `chunks/<chunk_id>.meta` (artifact hash, encoder version, safety flags)

Artifact content is implementation-defined but must be:

- deterministic
- self-validatable (hash/size/version)
- ignorable (advisory cache only)

## 5. Deterministic merge rules (future)

When assembling output from mixed reused/rebuilt chunks:

- Always order by **new manifest chunk order**.
- Re-emit container metadata deterministically (same normalization as full rebuild path).
- No dependence on filesystem iteration order.
- No timestamp/nonce randomness.

Result must match full fresh encode byte-for-byte for eligible scenarios, or fail closed to full rebuild.

## 6. Safety rules

Partial reuse allowed only when all are true:

- Cache meta matches config (`chunk_size`, `use_delta`, compressor version constraints).
- Receipt exists for candidate reusable chunk.
- Receipt hash + size match old manifest and candidate new chunk.
- Cached per-chunk artifact exists and passes artifact integrity checks.
- Scenario-level policy allows reuse (for now, noisy/high-entropy shifts should fail closed).

If any check fails:

- mark reason
- move chunk to rebuild set
- or fail closed to full rebuild if ambiguity crosses threshold.

## 7. Fallback rules

Fail closed to full rebuild when:

- manifest ambiguity/inconsistency
- widespread receipt mismatch/missing
- artifact corruption
- high-noise entropy shift (policy threshold)
- deterministic merge invariant cannot be proven

All fallbacks must be explicit in metrics/miss reasons.

## 8. Scenario guidance from current evidence

- **Append-only logs**: prime candidate. Expect high reuse except tail chunks.
- **Structured small-change**: likely good partial reuse target.
- **Mixed binaries small-change**: good candidate when binary chunk locality is stable.
- **Noisy-change**: keep conservative; default no/low reuse until robust proof.

## 9. Required tests before any implementation

Minimum pre-implementation test plan:

1. **Correctness parity**
   - partial-reuse output decompresses losslessly.
   - equals full fresh rebuild output for supported scenarios.

2. **Determinism**
   - repeated partial-reuse runs produce identical archives.
   - mixed reused/rebuilt merge is stable across runs/platforms.

3. **Safety/fail-closed**
   - missing/corrupt receipts/artifacts -> fallback.
   - config mismatch -> fallback.
   - noisy entropy shift -> fallback policy triggered.

4. **Attribution quality**
   - miss reasons are populated and consistent.
   - reusable-but-not-hit and partial opportunity counters remain accurate.

5. **Performance evidence**
   - report partial-reuse hit/rate + net time impact by scenario.
   - no regression in worst-case behavior when reuse is unavailable.

## 10. Implementation gate (explicit)

Do not implement partial reuse until:

- safety + determinism tests above exist and pass,
- scenario-level hit/benefit is demonstrated on realistic corpora,
- and a go decision is made for moving beyond verification-only behavior.

## 11. Behavior-change guardrail (must-fix)

Partial reuse must not change selected strategy/encoding vs full fresh build for
the same input and config.

- Required check: compare partial-reuse decision result to full fresh build
  decision result.
- If strategy/encoding differ: **fail closed to full rebuild**.
- Do not continue with mixed reused/rebuilt path when this mismatch is detected.

## 12. Byte-identical parity contract (must-fix)

Where reuse is allowed, final output must be byte-identical to full fresh build.

- Lossless-only parity is not sufficient for this gate.
- Required assertion: `partial_reuse_archive_bytes == fresh_archive_bytes`.
- If byte-identical parity fails: **fail closed to full rebuild** and record
  miss reason.

## 13. Chunk artifact schema (must-fix)

Per-chunk artifact metadata must include all required fields:

- `schema_version`
- `encoder_version`
- `chunk_hash`
- `size_bytes`
- `chunk_size`
- `use_delta`
- `profile_flags`
- `path_hint`
- `artifact_hash`

Any missing/malformed/incompatible required field must trigger rebuild/fallback.

## 14. Deterministic merge rule (must-fix)

Deterministic merge contract:

- Merge by new-manifest order only.
- Stable tie-break by `chunk_id`.
- No dependence on:
  - glob order
  - dict/hash-map iteration order
  - process scheduling/completion order

If deterministic ordering cannot be proven at runtime: **fail closed**.

## 15. Append-only boundary threshold (must-fix)

Boundary instability must be measurable and gated.

- Define a numeric threshold for boundary instability (for example, changed
  chunk ratio in non-tail regions, or boundary-shift count).
- If measured instability exceeds threshold: disable partial reuse for that run
  and perform full rebuild.

Threshold value and measurement method must be documented with benchmark
evidence before implementation.

## 16. Noisy fail-closed threshold (must-fix)

Noisy datasets must use explicit fail-closed criteria.

- Define entropy/rescan threshold(s), for example:
  - entropy-shift over baseline
  - rescan ratio threshold
- If threshold is exceeded: disable reuse and perform full rebuild.

Thresholds must be deterministic and scenario-tested before implementation.

## 17. Stale receipt policy (must-fix)

Receipt staleness handling must be explicit:

- Missing/stale per-chunk receipt => rebuild that chunk.
- If stale/missing receipt ratio exceeds configured threshold =>
  **full fail-closed rebuild**.

This policy must emit clear attribution metrics for auditability.
