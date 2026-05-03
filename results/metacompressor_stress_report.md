# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 104 | 211 | -50.7% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 164 | -62.8% | — | — | — | single-byte file |
| A-large_file | PASS | 663 | 1,183 | -44.0% | 1.679s | 0.244s | 61.8 MB | 10 MB structured log |
| A-many_small_files | PASS | 2,488 | 10,958 | -77.3% | 0.043s | 0.043s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 700 | 860 | -18.6% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 95 | 226 | -58.0% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 176 | -53.4% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 116 | 213 | -45.5% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,881 | 1,704 | 10.4% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,603 | 65,733 | -0.2% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 669 | 1,027 | -34.9% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 4,038 | 4,002 | 0.9% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 12,472 | 12,050 | 3.5% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 1,610 | 1,635 | -1.5% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 314 | 429 | -26.8% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 276 | 426 | -35.2% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,304 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 195 | 346 | -43.6% | 0.006s | 0.001s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 448 | 16,123 | -97.2% | 0.337s | 0.043s | 10.6 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| D-perf_large | PASS | 730 | 1,707 | -57.2% | 1.883s | 0.247s | 56.5 MB | 5 × 2 MB repetitive logs ≈ 10 MB |
| E-regression_structured_logs | PASS | 5,243 | 7,367 | -28.8% | — | — | — | Δ=-28.8% – within threshold |
| E-regression_nginx | PASS | 12,472 | 12,050 | 3.5% | — | — | — | Δ=3.5% – within threshold |
| E-regression_random | PASS | 65,603 | 65,733 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 1,610 | 1,636 | -1.6% | — | — | — | Δ=-1.6% – within threshold |
| E-regression_mixed | PASS | 314 | 420 | -25.2% | — | — | — | Δ=-25.2% – within threshold |

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by >10 %)

- **A-unique_content**: MC=1,881 TAR+ZSTD=1,704 Δ=10.4%  200 lines each with random payload – fallback path

## Slow Cases (compress > 5 s)

*(none)*

## Memory Spikes (peak > 200 MB)

*(none)*

## Summary

- Total tests recorded : 32
- Crashes              : 0
- Regressions          : 1
- Slow cases           : 0
- Memory spikes        : 0

**Final verdict: `STRESS_VALIDATED`**
