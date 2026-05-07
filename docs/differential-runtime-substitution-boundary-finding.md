# Differential Runtime Substitution Boundary Finding

Status: architecture note (research only)
Scope: no production behavior change

## Why current chunk-local artifacts fail parity

Current runtime substitution reuses chunk-local artifact bytes and rebuilds changed chunks, then re-assembles and recompresses the archive candidate. This is fail-closed and still returns `fresh_full_build`, but parity evidence shows candidate archives are not consistently byte-identical to fresh archives.

The key issue is boundary mismatch: chunk-local artifacts are too low-level to fully capture archive-global assembly effects.

## Evidence snapshot

From recent runtime experimental reports:

- repeated `byte_parity_mismatch` fallback reason
- stable first mismatch location at byte offset `10` in mismatch cases
- dominant `mismatch_stage: msgpack_structure`

Interpretation:

- offset 10 points to divergence in compressed payload region (post container header area), not magic/version corruption
- mismatch is driven by payload structure/assembly differences, not a simple chunk hash failure

## Likely global assembly dependencies

Parity drift likely comes from archive-global dependencies that chunk-local reuse does not preserve:

- msgpack object assembly/layout differences
- ordering and grouping effects during final payload construction
- zstd frame sensitivity to full payload shape
- possible cross-file / cross-sequence assembly state not encoded in per-chunk artifact blobs

## Options considered

1. `Abandon runtime substitution for now`
   Stop this path until artifact boundary is redesigned and parity can be proven stable.

2. `Reuse analysis/metadata only`
   Continue using differential manifests, receipts, and analysis-skip/decision reuse where safety is already demonstrated.

3. `Move artifact boundary higher: msgpack-object-level`
   Cache/reuse at a higher semantic boundary (post-assembly object segments rather than raw chunk bytes).

4. `Create deterministic assembly-state cache`
   Persist all assembly-relevant global state needed to replay byte-identical payload construction.

## Recommendation

Pause runtime artifact substitution now.

Keep and continue improving:

- differential layer (`manifest`, `receipts`, attribution)
- analysis-skip path
- decision metadata reuse

These provide safer and clearer wins under fail-closed constraints.
No further runtime substitution implementation should proceed until boundary redesign is specified and a new parity contract is validated.
