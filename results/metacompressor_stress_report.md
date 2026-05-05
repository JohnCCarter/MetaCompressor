# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 182 | -42.9% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 152 | -59.9% | — | — | — | single-byte file |
| A-many_small_files | PASS | 825 | 10,178 | -91.9% | 0.322s | 0.355s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 830 | -15.7% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 214 | -36.4% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 162 | -49.4% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 199 | -41.2% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,331 | 1,692 | -21.3% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,728 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 294 | 1,000 | -70.6% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,109 | 3,964 | -21.6% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,427 | 11,931 | -71.3% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 863 | 1,504 | -42.6% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 317 | 397 | -20.2% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 285 | 396 | -28.0% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,302 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 591 | -65.8% | 0.132s | 0.009s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 489 | 6,148 | -92.0% | 8.347s | 0.073s | 9.2 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| E-regression_structured_logs | PASS | 842 | 6,689 | -87.4% | — | — | — | Δ=-87.4% – within threshold |
| E-regression_nginx | PASS | 3,427 | 11,932 | -71.3% | — | — | — | Δ=-71.3% – within threshold |
| E-regression_random | PASS | 65,602 | 65,728 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 863 | 1,504 | -42.6% | — | — | — | Δ=-42.6% – within threshold |
| E-regression_mixed | PASS | 317 | 396 | -19.9% | — | — | — | Δ=-19.9% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

- D-perf_medium: compress 8.35s

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 30
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 1
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
