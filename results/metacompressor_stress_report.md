# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 107 | 192 | -44.3% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 151 | -59.6% | — | — | — | single-byte file |
| A-many_small_files | PASS | 836 | 10,600 | -92.1% | 0.246s | 0.479s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 714 | 830 | -14.0% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 211 | -35.5% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 162 | -49.4% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 200 | -41.5% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,326 | 1,691 | -21.6% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,728 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 309 | 1,001 | -69.1% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,124 | 3,986 | -21.6% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,467 | 11,932 | -70.9% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 876 | 1,503 | -41.7% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 320 | 392 | -18.4% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 294 | 411 | -28.5% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,302 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 556 | -63.7% | 0.053s | 0.014s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 465 | 10,677 | -95.6% | 2.583s | 0.072s | 7.1 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| E-regression_structured_logs | PASS | 1,718 | 6,689 | -74.3% | — | — | — | Δ=-74.3% – within threshold |
| E-regression_nginx | PASS | 3,467 | 11,931 | -70.9% | — | — | — | Δ=-70.9% – within threshold |
| E-regression_random | PASS | 65,602 | 65,728 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 876 | 1,500 | -41.6% | — | — | — | Δ=-41.6% – within threshold |
| E-regression_mixed | PASS | 320 | 403 | -20.6% | — | — | — | Δ=-20.6% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

*(none)*

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 30
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 0
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
