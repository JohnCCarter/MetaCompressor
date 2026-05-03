# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 215 | -51.6% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 162 | -62.3% | — | — | — | single-byte file |
| A-large_file | PASS | 387 | 1,182 | -67.3% | 11.528s | 0.275s | 133.6 MB | 10 MB structured log |
| A-many_small_files | PASS | 837 | 10,613 | -92.1% | 0.163s | 0.037s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 857 | -18.3% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 227 | -40.1% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 176 | -53.4% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 213 | -45.1% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,339 | 1,675 | -20.1% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 297 | 1,030 | -71.2% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,161 | 4,009 | -21.2% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,427 | 12,051 | -71.6% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 869 | 1,622 | -46.4% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 317 | 418 | -24.2% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 285 | 420 | -32.1% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,301 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 636 | -68.2% | 0.038s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 462 | 16,176 | -97.1% | 2.281s | 0.046s | 7.5 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 433 | 1,700 | -74.5% | 12.494s | 0.295s | 57.2 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 1,660 | 7,712 | -78.5% | — | — | — | Δ=-78.5% – within threshold |
| E-regression_nginx | PASS | 3,427 | 12,051 | -71.6% | — | — | — | Δ=-71.6% – within threshold |
| E-regression_random | PASS | 65,602 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 869 | 1,632 | -46.8% | — | — | — | Δ=-46.8% – within threshold |
| E-regression_mixed | PASS | 317 | 425 | -25.4% | — | — | — | Δ=-25.4% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

- A-large_file: compress 11.53s
- D-perf_large: compress 12.49s

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 2
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
