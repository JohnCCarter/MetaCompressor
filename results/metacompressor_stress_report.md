# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 208 | -50.0% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 163 | -62.6% | — | — | — | single-byte file |
| A-large_file | PASS | 659 | 1,183 | -44.3% | 2.104s | 0.236s | 76.9 MB | 10 MB structured log |
| A-many_small_files | PASS | 2,487 | 10,837 | -77.1% | 0.107s | 0.024s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 850 | -17.6% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 94 | 225 | -58.2% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 175 | -53.1% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 115 | 214 | -46.3% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,819 | 1,712 | 6.2% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,731 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 678 | 1,026 | -33.9% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,984 | 3,974 | 0.3% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 12,425 | 12,051 | 3.1% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 1,735 | 1,635 | 6.1% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 313 | 405 | -22.7% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 282 | 417 | -32.4% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,303 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 198 | 608 | -67.4% | 0.012s | 0.001s | 0.5 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 445 | 14,373 | -96.9% | 0.424s | 0.044s | 4.7 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 726 | 1,708 | -57.5% | 2.353s | 0.242s | 32.3 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 7,012 | 7,796 | -10.1% | — | — | — | Δ=-10.1% – within threshold |
| E-regression_nginx | PASS | 12,425 | 12,051 | 3.1% | — | — | — | Δ=3.1% – within threshold |
| E-regression_random | PASS | 65,602 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 1,735 | 1,635 | 6.1% | — | — | — | Δ=6.1% – within threshold |
| E-regression_mixed | PASS | 313 | 408 | -23.3% | — | — | — | Δ=-23.3% – within threshold |

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
