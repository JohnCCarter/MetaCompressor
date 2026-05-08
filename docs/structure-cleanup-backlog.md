# Structure Cleanup Backlog

Status: active cleanup queue
Scope: repository layout hygiene only (no runtime behavior changes).

## Why this exists

The repository has accumulated many generated reports and temporary profiling artifacts during performance iterations. This backlog keeps cleanup explicit and safe, aligned with `docs/repository-layout-policy.md`.

## Current priorities

1. **Results curation**
   - Keep high-signal phase outputs (`phase1`-`phase5`, hotpath summary, adoption docs).
   - Move stale exploratory report clutter to an archive bucket when approved.
2. **Temporary artifact containment**
   - Prefer `tmp/` for ephemeral scripts/outputs (legacy `tmp_*` still tolerated).
   - Ensure transient outputs remain gitignored unless intentionally curated.
3. **Root/zone hygiene**
   - Keep repo root intentional.
   - Keep runtime code in `metacompressor/`, tests in `metacompressor/tests/`, benchmark drivers in `benchmarks/`, reports in `results/`.

## Proposed safe cleanup sequence

1. Inventory `results/` into:
   - keep (active decision evidence)
   - archive (historical but retained)
   - drop (regenerable scratch)
2. Apply minimal move/delete pass in one dedicated cleanup PR.
3. Update ignore/lifecycle notes if recurring clutter patterns remain.

## Guardrails

- No runtime code moves during cleanup-only passes.
- No behavior changes mixed into structure-cleanup commits.
- Preserve reproducibility of accepted benchmark claims.
