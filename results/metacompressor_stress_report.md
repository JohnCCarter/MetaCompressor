# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 214 | -51.4% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 163 | -62.6% | — | — | — | single-byte file |
| A-large_file | PASS | 381 | 1,182 | -67.8% | 9.934s | 0.214s | 118.5 MB | 10 MB structured log |
| A-many_small_files | PASS | 837 | 10,934 | -92.3% | 0.116s | 0.028s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 844 | -17.1% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 94 | 227 | -58.6% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 177 | -53.7% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 115 | 213 | -46.0% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,813 | 1,723 | 5.2% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 297 | 1,028 | -71.1% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 4,004 | 3,991 | 0.3% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,190 | 12,050 | -73.5% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 850 | 1,635 | -48.0% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 313 | 402 | -22.1% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 282 | 420 | -32.9% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,301 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 198 | 617 | -67.9% | 0.024s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 445 | 10,941 | -95.9% | 1.965s | 0.037s | 7.3 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 431 | 1,709 | -74.8% | 8.178s | 0.236s | 54.3 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 1,646 | 7,680 | -78.6% | — | — | — | Δ=-78.6% – within threshold |
| E-regression_nginx | PASS | 3,190 | 12,051 | -73.5% | — | — | — | Δ=-73.5% – within threshold |
| E-regression_random | PASS | 65,602 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 850 | 1,635 | -48.0% | — | — | — | Δ=-48.0% – within threshold |
| E-regression_mixed | PASS | 313 | 424 | -26.2% | — | — | — | Δ=-26.2% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

- A-large_file: compress 9.93s
- D-perf_large: compress 8.18s

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 2
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
