# Template Hotpath Optimization Summary

Status: 2026-05-08
Scope: safe micro-optimizations in template/tokenization hotpath only (no wire-format, strategy, runtime substitution, or cache-return changes).

## Accepted commits

- `b8df817` - `perf(template): cache normalized text segments`
- `e6e8cf6` - `perf(template): reduce legacy tokenization object churn`
- `e41162e` - `perf(template): optimize tokenization hot path`
- `a4929db` - `perf(template): optimize variable scan hot path`
- `d31d881` - `perf(template): defer kv tag allocation in variable scan`
- `27357ff` - `perf(template): defer kv key normalization until selection`
- `5d2bdae` - `perf(template): use hit-biased counter increments in tokenize loop`

All accepted passes preserved:

- `output_size_bytes` unchanged
- `selected_mode` unchanged (`corpus_template_columnar_v2`)
- correctness/determinism pass
- full `pytest`, `ruff`, and `black --check` green

## Rejected candidates

- `_scan_text_line`: cached `line_len = len(line)` loop guard
  - Rejected due to median regressions in primary hotspot and build/template layers.
- `_tokenize_one_file` cache lookup: `try/except KeyError` for `local_tok_cache`
  - Rejected due to large `template_cache_lookup_time_ms` regression and broader build/template regressions.

Both candidates were reverted immediately; no commit.

## Before/after trend (100MB workload, median)

Representative progression from early pass to latest accepted pass:

- `tokenization_time_ms`: `39,352 -> 29,064` (about 26% lower)
- `template_extract_time_ms`: `51,306 -> 33,286` (about 35% lower)
- `mc_selected_build_time_ms`: `144,198 -> 96,229` (about 33% lower, comparing pass medians)

Latest rejected pass confirms diminishing returns and higher noise/regression risk for remaining tiny local changes.

## Remaining hotspots (latest stable profile family)

Top remaining timing layers are still:

- `mc_selected_build_time_ms`
- `transform_call_time_ms`
- `template_extract_time_ms`
- `tokenization_time_ms`

Within template substeps, the largest buckets are now relatively small and closer together:

- `template_grouping_time_ms`
- `template_hash_time_ms`
- `line_split_time_ms`
- `template_cache_lookup_time_ms`

This pattern suggests mature local hotpath tuning with fewer clear low-risk wins left.

## Recommendation

- Stop forcing additional micro-opts for now unless new profiler evidence shows a clear, repeatable hotspot with strong expected gain.
- Prefer next investigation in one of these directions:
  - ZSTD-affine shaping (advisory/controlled experimentation)
  - higher-level build-path profiling (beyond micro token-level tweaks)

In short: preserve the current safety-first gate system and move focus up one level when hotspot evidence is ambiguous.
