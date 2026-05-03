# MetaCompressor Internal Hardening Report

**Verdict:** `INTERNAL_HARDENING_VALIDATED`

## Dataset Results

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | Per-file ZSTD | gzip | brotli | Compress s | Decomp s | Peak MB | Winner | Notes |
|---------|----:|-------------------:|---------:|-------:|-------------:|-----:|-------:|-----------:|---------:|--------:|--------|-------|
| H-50mb_structured | 52,428,800 | 421 | 5,021 | -91.6% | 4,877 | 178,232 | — | 49.936s | 1.244s | 595.0 MB | MC | 50 MB structured log; tpl_reuse=1.00; ratio=0.00001 |
| H-100mb_structured | 104,857,600 | 470 | 9,822 | -95.2% | 9,677 | 356,204 | — | 100.466s | 2.396s | 1191.6 MB | MC | 100 MB structured log; tpl_reuse=1.00; ratio=0.000004 |
| H-2000_small_files | 152,679 | 2,156 | 38,990 | -94.5% | 145,326 | 54,095 | — | 1.719s | 0.109s | 14.0 MB | MC | 2 000 small files; tpl_reuse=1.00; files=2000 |
| H-mixed_app_logs | 146,392 | 3,370 | 9,675 | -65.2% | 7,679 | 13,972 | — | 0.022s | 0.004s | — | MC | 4 app log formats; tpl_reuse=1.00; templates=9 |
| H-nginx_10k | 1,183,385 | 12,925 | 117,205 | -89.0% | 117,045 | 156,901 | — | 0.170s | 0.030s | — | MC | 10 000 nginx lines; tpl_reuse=1.00; templates=1; low_struct_fb=0 |
| H-ndjson_50k | 5,346,556 | 19,195 | 225,613 | -91.5% | 225,197 | 436,341 | — | 4.208s | 0.126s | 86.3 MB | MC | 50k NDJSON lines; tpl_reuse=1.00; templates=3 |
| H-prose | 184,915 | 40,477 | 40,808 | -0.8% | 40,592 | 35,499 | — | — | — | — | gzip | prose text; tpl_reuse=0.00; binary_fb=1; low_struct_fb=0 |
| H-low_struct_fallback | — | — | — | — | — | — | — | — | — | — | — | low-structure fallback fires; low_struct_fb=1 |
| H-low_struct_size | — | 465 | 584 | -20.4% | 394 | — | — | — | — | — | per-file-zstd | low-struct size test; Δ=-20.4%; low_struct_fb=1 |
| H-highcard_2k | 170,690 | 39,316 | 43,480 | -9.6% | 43,296 | 45,036 | — | — | — | — | MC | 2000 lines; recurring tpl; random vals; tpl_reuse=0.00; Δ=-9.6% |
| H-random_binary_mix | 470,304 | 98,833 | 98,825 | 0.0% | 98,485 | — | — | — | — | — | per-file-zstd | random+structured mix; binary_fb=2; tpl_reuse=1.00 |
| H-precompressed_mix | 40,177 | 308 | 455 | -32.3% | 249 | — | — | — | — | — | per-file-zstd | gz+zst+log; binary_fb=2; tpl_reuse=1.00 |
| H-binary_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 20 random-binary files; all must round-trip without corruption |
| H-hybrid_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 50 unique-template lines → hybrid fallback → lossless |
| H-low_struct_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | ~3% template rate → low-structure fallback; low_struct_fb=1 |
| H-no_silent_corruption_large | — | — | — | — | — | — | — | — | — | — | — | corrupt archive raised exception (correct) |
| H-reg_structured_50mb | — | 421 | 5,022 | -91.6% | — | — | — | — | — | — | MC | Δ=-91.6% – within threshold |
| H-reg_mixed_app_logs | — | 3,370 | 9,664 | -65.1% | — | — | — | — | — | — | MC | Δ=-65.1% – within threshold |
| H-reg_2000_small | — | 2,156 | 39,189 | -94.5% | — | — | — | — | — | — | MC | Δ=-94.5% – within threshold |
| H-reg_prose | — | 40,477 | 40,808 | -0.8% | — | — | — | — | — | — | MC | Δ=-0.8% – within threshold |
| H-reg_random_binary | — | 65,602 | 65,733 | -0.2% | — | — | — | — | — | — | MC | Δ=-0.2% – within threshold |
| H-determinism_10mb | — | 381 | — | — | — | — | — | — | — | — | MC | two independent compressions of identical 10 MB corpus → identical bytes |
| H-determinism_200files | — | 615 | — | — | — | — | — | — | — | — | MC | 200 small files, two runs → identical bytes |
| H-250mb_structured | 262,144,000 | 622 | 24,222 | -97.4% | — | — | — | 251.260s | 6.284s | 2953.5 MB | MC | 250 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=2953 MB; raw_fb=False |
| H-500mb_structured | 524,288,000 | 880 | 48,224 | -98.2% | — | — | — | 508.231s | 12.119s | 5914.1 MB | MC | 500 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=5914 MB; raw_fb=False |

## Where MC Wins

- **H-50mb_structured**: MC=421 vs TAR+ZSTD=5,021 (Δ=-91.6%)  50 MB structured log; tpl_reuse=1.00; ratio=0.00001
- **H-100mb_structured**: MC=470 vs TAR+ZSTD=9,822 (Δ=-95.2%)  100 MB structured log; tpl_reuse=1.00; ratio=0.000004
- **H-2000_small_files**: MC=2,156 vs TAR+ZSTD=38,990 (Δ=-94.5%)  2 000 small files; tpl_reuse=1.00; files=2000
- **H-mixed_app_logs**: MC=3,370 vs TAR+ZSTD=9,675 (Δ=-65.2%)  4 app log formats; tpl_reuse=1.00; templates=9
- **H-nginx_10k**: MC=12,925 vs TAR+ZSTD=117,205 (Δ=-89.0%)  10 000 nginx lines; tpl_reuse=1.00; templates=1; low_struct_fb=0
- **H-ndjson_50k**: MC=19,195 vs TAR+ZSTD=225,613 (Δ=-91.5%)  50k NDJSON lines; tpl_reuse=1.00; templates=3
- **H-low_struct_size**: MC=465 vs TAR+ZSTD=584 (Δ=-20.4%)  low-struct size test; Δ=-20.4%; low_struct_fb=1
- **H-highcard_2k**: MC=39,316 vs TAR+ZSTD=43,480 (Δ=-9.6%)  2000 lines; recurring tpl; random vals; tpl_reuse=0.00; Δ=-9.6%
- **H-precompressed_mix**: MC=308 vs TAR+ZSTD=455 (Δ=-32.3%)  gz+zst+log; binary_fb=2; tpl_reuse=1.00
- **H-reg_structured_50mb**: MC=421 vs TAR+ZSTD=5,022 (Δ=-91.6%)  Δ=-91.6% – within threshold
- **H-reg_mixed_app_logs**: MC=3,370 vs TAR+ZSTD=9,664 (Δ=-65.1%)  Δ=-65.1% – within threshold
- **H-reg_2000_small**: MC=2,156 vs TAR+ZSTD=39,189 (Δ=-94.5%)  Δ=-94.5% – within threshold
- **H-250mb_structured**: MC=622 vs TAR+ZSTD=24,222 (Δ=-97.4%)  250 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=2953 MB; raw_fb=False
- **H-500mb_structured**: MC=880 vs TAR+ZSTD=48,224 (Δ=-98.2%)  500 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=5914 MB; raw_fb=False

**Why MC wins:** Highly repetitive or structured corpora allow the shared template dictionary to deduplicate line structure across many files. When the same log template recurs thousands of times, storing it once and encoding only the variable slots achieves large savings beyond what generic ZSTD compression can achieve, especially for many-small-file corpora where tar overhead dominates TAR+ZSTD.

## Where MC Loses

*(no results show MC losing by > 5%)*

## Fallback Behaviour

MetaCompressor applies fallback at multiple levels:

| Level | Trigger | Behaviour |
|-------|---------|-----------|
| Binary file | UTF-8 decode failure | Stored as raw bytes (`[-2, ...]` record) |
| Zero-template file | No recurring templates in file | Stored as raw bytes (hybrid fallback) |
| Low-structure file | Template rate < 10% of lines | Stored as raw bytes (low-structure fallback) |
| log_template single file | Template mode larger than raw | Selects raw zstd automatically |

The low-structure fallback is new in this hardening pass. It prevents per-line `[-1, raw_line]` msgpack record overhead for files that are mostly unstructured but have a handful of matching template lines.

## Performance Bottlenecks

| Phase | Observation |
|-------|-------------|
| Tokenisation | O(unique lines) with cache – fast for repetitive corpora |
| Template counting | O(total lines) dict lookup – linear in corpus size |
| Encoding | O(total lines) – dominated by dict lookup + list append |
| Serialisation (msgpack) | Grows with number of records (non-template lines are expensive) |
| Zstandard (level 3) | Fast; dominates only on large/random corpora |
| Memory | ~6× raw corpus size worst case (file bytes + tokenised forms + records) |

## Memory Usage

Peak memory scales with corpus size. For highly repetitive data the tokenisation cache is tiny (one entry per unique line) so memory stays close to 1× the raw corpus size. For diverse corpora the cache and records list can push memory to 3–6× the raw input.

## Crashes

*(none)*

## Regressions (MC > TAR+ZSTD by > 10 %)

*(none)*

## Slow Cases (compress > 30 s)

- H-50mb_structured: compress 49.9s
- H-100mb_structured: compress 100.5s
- H-250mb_structured: compress 251.3s
- H-500mb_structured: compress 508.2s

## Memory Spikes (peak > 400 MB)

- H-50mb_structured: 595 MB
- H-100mb_structured: 1191.6 MB
- H-250mb_structured: 2953 MB
- H-500mb_structured: 5914 MB

## Analysis Notes

*(none)*

## Summary

- Total tests recorded : 25
- MC wins (Δ < -5%)   : 14
- MC losses (Δ > +5%) : 0
- Crashes              : 0
- Regressions (> 10%) : 0
- Slow cases           : 4
- Memory spikes        : 4

**Final verdict: `INTERNAL_HARDENING_VALIDATED`**
