# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 107 | 204 | -47.5% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 158 | -61.4% | — | — | — | single-byte file |
| A-many_small_files | PASS | 836 | 10,931 | -92.4% | 0.098s | 0.030s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 714 | 839 | -14.9% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 223 | -39.0% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 170 | -51.8% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 205 | -42.9% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,327 | 1,698 | -21.8% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,973 | -0.6% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 309 | 1,021 | -69.7% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,122 | 3,996 | -21.9% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,467 | 11,943 | -71.0% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 876 | 1,503 | -41.7% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 320 | 395 | -19.0% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 294 | 434 | -32.3% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,302 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 535 | -62.2% | 0.037s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 465 | 14,847 | -96.9% | 2.646s | 0.051s | 6.8 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| E-regression_structured_logs | PASS | 1,718 | 7,149 | -76.0% | — | — | — | Δ=-76.0% – within threshold |
| E-regression_nginx | PASS | 3,467 | 11,943 | -71.0% | — | — | — | Δ=-71.0% – within threshold |
| E-regression_random | PASS | 65,602 | 65,720 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 876 | 1,502 | -41.7% | — | — | — | Δ=-41.7% – within threshold |
| E-regression_mixed | PASS | 320 | 409 | -21.8% | — | — | — | Δ=-21.8% – within threshold |

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
