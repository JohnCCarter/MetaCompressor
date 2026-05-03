# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 208 | -50.0% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 163 | -62.6% | — | — | — | single-byte file |
| A-large_file | PASS | 663 | 1,183 | -44.0% | 7.094s | 0.244s | 98.3 MB | 10 MB structured log |
| A-many_small_files | PASS | 2,488 | 11,131 | -77.6% | 0.063s | 0.044s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 705 | 837 | -15.8% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 95 | 228 | -58.3% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 177 | -53.7% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 116 | 215 | -46.0% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,887 | 1,691 | 11.6% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,603 | 65,733 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 669 | 1,031 | -35.1% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 4,282 | 3,978 | 7.6% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 12,495 | 12,051 | 3.7% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 1,610 | 1,636 | -1.6% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 321 | 423 | -24.1% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 276 | 437 | -36.8% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,304 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 195 | 391 | -50.1% | 0.016s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 448 | 8,521 | -94.7% | 1.359s | 0.044s | 18.4 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 730 | 1,710 | -57.3% | 7.042s | 0.249s | 101.8 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 5,243 | 7,753 | -32.4% | — | — | — | Δ=-32.4% – within threshold |
| E-regression_nginx | PASS | 12,495 | 12,051 | 3.7% | — | — | — | Δ=3.7% – within threshold |
| E-regression_random | PASS | 65,603 | 65,731 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 1,610 | 1,636 | -1.6% | — | — | — | Δ=-1.6% – within threshold |
| E-regression_mixed | PASS | 321 | 400 | -19.8% | — | — | — | Δ=-19.8% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

- **A-unique_content**: MC=1,887 TAR+ZSTD=1,691 Δ=11.6%  200 lines each with random payload – fallback path

## Slow Cases (compress > 5 s)

- A-large_file: compress 7.09s
- D-perf_large: compress 7.04s

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 1
- Slow cases           : 2
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
