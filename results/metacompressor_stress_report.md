# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 216 | -51.9% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 161 | -62.1% | — | — | — | single-byte file |
| A-large_file | PASS | 663 | 1,183 | -44.0% | 1.727s | 0.262s | 61.8 MB | 10 MB structured log |
| A-many_small_files | PASS | 2,488 | 10,616 | -76.6% | 0.035s | 0.024s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 705 | 838 | -15.9% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 95 | 229 | -58.5% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 177 | -53.7% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 116 | 217 | -46.5% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,819 | 1,702 | 6.9% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,603 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 669 | 1,030 | -35.0% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 4,306 | 3,982 | 8.1% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 12,472 | 12,052 | 3.5% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 1,610 | 1,635 | -1.5% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 314 | 408 | -23.0% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 276 | 419 | -34.1% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,304 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 195 | 576 | -66.1% | 0.006s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 448 | 6,775 | -93.4% | 0.343s | 0.043s | 10.6 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 730 | 1,714 | -57.4% | 1.944s | 0.239s | 56.5 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 5,243 | 7,793 | -32.7% | — | — | — | Δ=-32.7% – within threshold |
| E-regression_nginx | PASS | 12,472 | 12,050 | 3.5% | — | — | — | Δ=3.5% – within threshold |
| E-regression_random | PASS | 65,603 | 65,733 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 1,610 | 1,633 | -1.4% | — | — | — | Δ=-1.4% – within threshold |
| E-regression_mixed | PASS | 314 | 417 | -24.7% | — | — | — | Δ=-24.7% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

*(none)*

## Slow Cases (compress > 5 s)

*(none)*

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 0
- Slow cases           : 0
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
