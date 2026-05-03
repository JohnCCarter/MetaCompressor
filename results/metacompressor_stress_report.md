# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 211 | -50.7% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 164 | -62.8% | — | — | — | single-byte file |
| A-large_file | PASS | 663 | 1,182 | -43.9% | 7.038s | 0.244s | 98.3 MB | 10 MB structured log |
| A-many_small_files | PASS | 2,488 | 10,865 | -77.1% | 0.056s | 0.042s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 839 | -16.6% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 95 | 226 | -58.0% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 174 | -52.9% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 116 | 216 | -46.3% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,884 | 1,719 | 9.6% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,603 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 669 | 1,027 | -34.9% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 4,285 | 3,973 | 7.9% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 12,495 | 12,051 | 3.7% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 1,610 | 1,621 | -0.7% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 321 | 403 | -20.3% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 276 | 431 | -36.0% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,304 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 195 | 602 | -67.6% | 0.016s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 448 | 13,534 | -96.7% | 1.365s | 0.044s | 18.4 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 730 | 1,706 | -57.2% | 7.102s | 0.252s | 101.8 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 5,243 | 7,327 | -28.4% | — | — | — | Δ=-28.4% – within threshold |
| E-regression_nginx | PASS | 12,495 | 12,051 | 3.7% | — | — | — | Δ=3.7% – within threshold |
| E-regression_random | PASS | 65,603 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 1,610 | 1,633 | -1.4% | — | — | — | Δ=-1.4% – within threshold |
| E-regression_mixed | PASS | 321 | 418 | -23.2% | — | — | — | Δ=-23.2% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

- A-large_file: compress 7.04s
- D-perf_large: compress 7.10s

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 2
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
