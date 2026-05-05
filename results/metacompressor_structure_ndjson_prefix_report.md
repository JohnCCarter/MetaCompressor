# MetaCompressor Structure Detection — NDJSON / JSON Prefix Report

**Verdict:** `STRUCTURE_CHANGE_VALIDATED` (micro-benchmark + unit tests; full acceptance hardening not re-run for this note)

**Scope:** Follow-up after production validation — improve JSON/NDJSON detection when a **non-JSON prefix** appears before `{`/`[` (common NDJSON / log-shipping lines), and fix **leading whitespace** before JSON. No lossy compression; deterministic leftmost-parse rule.

**Code:** `metacompressor/corpus_template.py` — `_analyze_json_line`, helper `_line_analysis_from_json_leaves`. **Tests:** `metacompressor/tests/test_corpus_template.py` (`test_timestamp_prefixed_ndjson_triggers_json_detection`, `test_leading_whitespace_before_json_still_parses`).

## Dataset Results (synthetic micro-corpus, n=50)

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | json_lines | tpl_reuse | templates | Notes |
|---------|----:|-------------------:|---------:|--------:|-----------:|----------:|----------:|-------|
| R-prefixed_ndjson_n50 | 4,340 | 297 | 291 | +2.06% | 50 | 0.98 | 1 | Same JSON body per line; fixed ISO prefix before `{` |
| R-plain_ndjson_n50 | 3,290 | 279 | 272 | +2.57% | 50 | 0.98 | 1 | Same JSON lines without timestamp prefix |

*Delta %* = `(MC − TAR+ZSTD) / TAR+ZSTD` — positive means MC archive is **larger** than TAR+ZSTD on this tiny corpus (expected: tar overhead amortises poorly over 50 short lines).

## Qualitative “before” behaviour (prefix case)

| Aspect | Before | After |
|--------|--------|--------|
| Line `2026-05-04T12:00:00Z {"service":"api",...}` | JSON path skipped (`strip()[0]` not `{`) → text tokeniser | Parsed as JSON from first `{`; `json_lines_detected` counts the line |
| Line `  {"a":1}` | Mismatch: strip allowed `{` but parser started at index `0` → often failed | Parser tries each `{`/`[` from first non-ws; consistent with strip intent |

## Where MC Wins

*(not claimed on these two micro-rows — both show small MC-vs-TAR+ZSTD **loss** on size; the win is **correct structure class** for downstream template/columnar paths on prefixed NDJSON.)*

## Where MC Loses

| Dataset | MC | TAR+ZSTD | Delta % | Notes |
|---------|---:|---------:|--------:|-------|
| R-prefixed_ndjson_n50 | 297 | 291 | +2.06% | Micro-corpus; not representative of large NDJSON corpora |
| R-plain_ndjson_n50 | 279 | 272 | +2.57% | Same |

## Fallback Behaviour

Unchanged in this change. Automatic `raw_tar_zstd` fallback still applies when template output exceeds `_CORPUS_FALLBACK_THRESHOLD` × TAR+ZSTD size.

## Performance / Memory

| Topic | Observation |
|-------|----------------|
| Parse rule | O(n) scan per line for leftmost valid JSON suffix — acceptable for line-oriented logs |
| Memory | **No change** in this commit (columnar streaming / TAR baseline paths untouched) |
| Full acceptance (`benchmarks/acceptance_hardening.py`) | **Not re-executed** for this document — run separately to refresh `metacompressor_acceptance_hardening.md` |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by > 10 %)

*(not evaluated beyond `pytest metacompressor/tests` — all tests passing at report time)*

## Remaining Weak Cases

| Case | Notes |
|------|-------|
| **Varying prefix per line** (e.g. unique timestamp each line) | Prefix literals differ → separate template keys unless prefix slots are normalised (lossless design choice) |
| **Fuzzy merge** | `normalized_skeleton` groups are still **metrics-only**; `tpl_to_id` is keyed by `template_parts` |
| **TAR+ZSTD baseline build** | Still materialises full baseline in memory for size comparison — separate optimisation track |
| **Acceptance goal** (≥3 realistic datasets ≥10% vs TAR+ZSTD) | Requires full benchmark run + further corpus-template work |

## Analysis Notes

- Real-world NDJSON with **shared** prefix (or no prefix) benefits most from JSON extraction + shared templates.
- For **acceptance** reporting, continue to use `benchmarks/acceptance_hardening.py` and committed snapshots under `results/` where applicable.

## Summary

- Micro-datasets recorded : 2
- MC smaller than TAR+ZSTD (Δ < 0) : 0
- MC larger than TAR+ZSTD on micro-rows : 2 (expected at n=50)
- Code paths touched : JSON line analysis only (+ tests)
- Full acceptance / production validation re-run : **not** part of this file

**Final note:** This report documents a **targeted structure-detection** improvement; it does **not** replace `metacompressor_production_validation.md` or `metacompressor_internal_hardening_report.md`.
