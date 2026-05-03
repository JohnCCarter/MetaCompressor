# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 206 | -49.5% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 165 | -63.0% | — | — | — | single-byte file |
| A-large_file | PASS | 387 | 1,185 | -67.3% | 11.404s | 0.248s | 133.6 MB | 10 MB structured log |
| A-many_small_files | PASS | 837 | 10,804 | -92.3% | 0.157s | 0.025s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 850 | -17.6% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 227 | -40.1% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 176 | -53.4% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 215 | -45.6% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,340 | 1,692 | -20.8% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 297 | 1,017 | -70.8% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,155 | 3,940 | -19.9% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,427 | 12,050 | -71.6% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 869 | 1,637 | -46.9% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 317 | 400 | -20.8% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 285 | 438 | -34.9% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,301 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 389 | -48.1% | 0.036s | 0.010s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 462 | 14,639 | -96.8% | 2.247s | 0.044s | 7.6 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 433 | 1,709 | -74.7% | 12.163s | 0.255s | 57.2 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 1,660 | 7,324 | -77.3% | — | — | — | Δ=-77.3% – within threshold |
| E-regression_nginx | PASS | 3,427 | 12,056 | -71.6% | — | — | — | Δ=-71.6% – within threshold |
| E-regression_random | PASS | 65,602 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 869 | 1,621 | -46.4% | — | — | — | Δ=-46.4% – within threshold |
| E-regression_mixed | PASS | 317 | 419 | -24.3% | — | — | — | Δ=-24.3% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

- A-large_file: compress 11.40s
- D-perf_large: compress 12.16s

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 2
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
