# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 208 | -50.0% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 164 | -62.8% | — | — | — | single-byte file |
| A-many_small_files | PASS | 837 | 10,931 | -92.3% | 0.202s | 0.045s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 840 | -16.7% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 226 | -39.8% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 176 | -53.4% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 214 | -45.3% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,322 | 1,684 | -21.5% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 294 | 1,028 | -71.4% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,115 | 3,941 | -21.0% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,427 | 12,050 | -71.6% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 866 | 1,621 | -46.6% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 317 | 401 | -20.9% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 285 | 431 | -33.9% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,302 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 595 | -66.1% | 0.045s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 462 | 10,458 | -95.6% | 2.664s | 0.060s | 6.6 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| E-regression_structured_logs | PASS | 1,659 | 7,771 | -78.7% | — | — | — | Δ=-78.7% – within threshold |
| E-regression_nginx | PASS | 3,427 | 12,051 | -71.6% | — | — | — | Δ=-71.6% – within threshold |
| E-regression_random | PASS | 65,602 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 866 | 1,622 | -46.6% | — | — | — | Δ=-46.6% – within threshold |
| E-regression_mixed | PASS | 317 | 418 | -24.2% | — | — | — | Δ=-24.2% – within threshold |

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
