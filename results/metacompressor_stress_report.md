# MetaCompressor Stress Report

**Verdict:** `STRESS_VALIDATED`

## Results Table

| Test | Status | MC size | TAR+ZSTD size | Delta% | Compress s | Decomp s | Peak MB | Notes |
|------|:------:|--------:|--------------:|-------:|-----------:|---------:|--------:|-------|
| A-empty_file | PASS | 107 | 189 | -43.4% | — | — | — | empty file in corpus – must round-trip with zero bytes |
| A-single_byte | PASS | 61 | 152 | -59.9% | — | — | — | single-byte file |
| A-many_small_files | PASS | 836 | 10,262 | -91.9% | 0.217s | 0.433s | — | 500 small structured log files |
| A-mixed_text_binary | PASS | 714 | 829 | -13.9% | — | — | — | text + binary in same corpus |
| A-long_lines | PASS | 136 | 210 | -35.2% | — | — | — | 10 000-char lines |
| A-no_trailing_newline | PASS | 82 | 164 | -50.0% | — | — | — | file with no trailing newline |
| A-repetitive_content | PASS | 117 | 198 | -40.9% | — | — | — | 5 000 identical lines; ratio=0.0007; tpl_reuse=1.00 |
| A-unique_content | PASS | 1,331 | 1,711 | -22.2% | — | — | — | 200 lines each with random payload – fallback path |
| B-random_data | PASS | 65,602 | 65,964 | -0.5% | — | — | — | fully random binary – binary_fallback expected |
| B-nearly_identical_lines | PASS | 309 | 1,002 | -69.2% | — | — | — | 500 lines w/ same template; reuse_rate=1.00 |
| B-high_cardinality | PASS | 3,122 | 3,968 | -21.3% | — | — | — | unique lines – fallback expected; no crash |
| B-truncated_archive | PASS | — | — | — | — | — | — | truncated archive → exception raised |
| B-invalid_magic | PASS | — | — | — | — | — | — | invalid magic → ValueError raised |
| B-broken_msgpack | PASS | — | — | — | — | — | — | corrupt msgpack payload → exception raised |
| B-too_short | PASS | — | — | — | — | — | — | <5 byte input → ValueError |
| B-bad_version | PASS | — | — | — | — | — | — | version 0xFF → ValueError |
| B-log_random_fallback | PASS | 8,225 | — | — | — | — | — | random bytes → log_template raw fallback |
| B-no_silent_corruption | PASS | — | — | — | — | — | — | corrupt archive raised exception (correct behaviour) |
| C-nginx_logs | PASS | 3,467 | 11,930 | -70.9% | — | — | — | 1 000 nginx lines; tpl_reuse=1.00 |
| C-json_ndjson | PASS | 876 | 1,507 | -41.9% | — | — | — | NDJSON + JSON config |
| C-mixed_formats | PASS | 320 | 403 | -20.6% | — | — | — | nginx + app log + ndjson + markdown |
| C-precompressed | PASS | 294 | 421 | -30.2% | — | — | — | gz + zip + log; binary_fallback=2 |
| C-all_binary_fallback | PASS | 5,302 | — | — | — | — | — | 5 random-binary files → all binary_fallback |
| D-perf_small | PASS | 202 | 463 | -56.4% | 0.205s | 0.016s | 0.4 MB | 10 × 5 KB structured logs |
| D-perf_medium | PASS | 465 | 9,386 | -95.0% | 4.138s | 0.081s | 7.1 MB | 20 × ~100 KB structured logs ≈ 2 MB |
| E-regression_structured_logs | PASS | 1,718 | 6,643 | -74.1% | — | — | — | Δ=-74.1% – within threshold |
| E-regression_nginx | PASS | 3,467 | 11,931 | -70.9% | — | — | — | Δ=-70.9% – within threshold |
| E-regression_random | PASS | 65,602 | 65,728 | -0.2% | — | — | — | Δ=-0.2% – within threshold |
| E-regression_json | PASS | 876 | 1,498 | -41.5% | — | — | — | Δ=-41.5% – within threshold |
| E-regression_mixed | PASS | 320 | 392 | -18.4% | — | — | — | Δ=-18.4% – within threshold |

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
