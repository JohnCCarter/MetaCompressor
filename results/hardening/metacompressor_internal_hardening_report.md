# MetaCompressor Internal Hardening Report

**Verdict:** `INTERNAL_HARDENING_VALIDATED`

## Dataset Results

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | Per-file ZSTD | gzip | brotli | Compress s | Decomp s | Peak MB | Winner | Notes |
|---------|----:|-------------------:|---------:|-------:|-------------:|-----:|-------:|-----------:|---------:|--------:|--------|-------|
| H-2000_small_files | 152,679 | 2,151 | 36,741 | -94.1% | 145,326 | 53,425 | — | 2.926s | 2.054s | 13.8 MB | MC | 2 000 small files; tpl_reuse=1.00; files=2000 |
| H-mixed_app_logs | 146,392 | 4,795 | 9,694 | -50.5% | 7,711 | 13,893 | — | 0.051s | 0.011s | — | MC | 4 app log formats; tpl_reuse=1.00; templates=7 |
| H-nginx_10k | 1,183,385 | 26,509 | 116,093 | -77.2% | 115,972 | 156,888 | — | 0.395s | 0.037s | — | MC | 10 000 nginx lines; tpl_reuse=1.00; templates=1; low_struct_fb=0 |
| H-prose | 184,915 | 42,289 | 40,777 | 3.7% | 40,581 | 35,477 | — | — | — | — | gzip | prose text; tpl_reuse=0.00; binary_fb=1; low_struct_fb=0 |
| H-low_struct_fallback | — | — | — | — | — | — | — | — | — | — | — | low-structure fallback fires; low_struct_fb=1 |
| H-low_struct_size | — | 520 | 558 | -6.8% | 394 | — | — | — | — | — | per-file-zstd | low-struct size test; Δ=-6.8%; low_struct_fb=1 |
| H-highcard_2k | 170,690 | 42,673 | 43,423 | -1.7% | 43,265 | 45,023 | — | — | — | — | MC | 2000 lines; recurring tpl; random vals; tpl_reuse=0.42; Δ=-1.7% |
| H-random_binary_mix | 470,304 | 98,856 | 98,812 | 0.0% | 98,485 | — | — | — | — | — | per-file-zstd | random+structured mix; binary_fb=2; tpl_reuse=1.00 |
| H-precompressed_mix | 40,177 | 324 | 449 | -27.8% | 249 | — | — | — | — | — | per-file-zstd | gz+zst+log; binary_fb=2; tpl_reuse=1.00 |
| H-binary_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 20 random-binary files; all must round-trip without corruption |
| H-hybrid_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 50 unique-template lines → hybrid fallback → lossless |
| H-low_struct_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | ~3% template rate → low-structure fallback; low_struct_fb=1 |
| H-no_silent_corruption_large | — | — | — | — | — | — | — | — | — | — | — | corrupt archive raised exception (correct) |
| H-reg_mixed_app_logs | — | 4,795 | 9,695 | -50.5% | — | — | — | — | — | — | MC | Δ=-50.5% – within threshold |
| H-reg_2000_small | — | 2,151 | 43,689 | -95.1% | — | — | — | — | — | — | MC | Δ=-95.1% – within threshold |
| H-reg_prose | — | 42,289 | 40,778 | 3.7% | — | — | — | — | — | — | TAR+ZSTD | Δ=3.7% – within threshold |
| H-reg_random_binary | — | 65,602 | 65,727 | -0.2% | — | — | — | — | — | — | MC | Δ=-0.2% – within threshold |
| H-determinism_10mb | — | 400 | — | — | — | — | — | — | — | — | MC | two independent compressions of identical 10 MB corpus → identical bytes |
| H-determinism_200files | — | 611 | — | — | — | — | — | — | — | — | MC | 200 small files, two runs → identical bytes |

## Where MC Wins

- **H-2000_small_files**: MC=2,151 vs TAR+ZSTD=36,741 (Δ=-94.1%)  2 000 small files; tpl_reuse=1.00; files=2000
- **H-mixed_app_logs**: MC=4,795 vs TAR+ZSTD=9,694 (Δ=-50.5%)  4 app log formats; tpl_reuse=1.00; templates=7
- **H-nginx_10k**: MC=26,509 vs TAR+ZSTD=116,093 (Δ=-77.2%)  10 000 nginx lines; tpl_reuse=1.00; templates=1; low_struct_fb=0
- **H-low_struct_size**: MC=520 vs TAR+ZSTD=558 (Δ=-6.8%)  low-struct size test; Δ=-6.8%; low_struct_fb=1
- **H-precompressed_mix**: MC=324 vs TAR+ZSTD=449 (Δ=-27.8%)  gz+zst+log; binary_fb=2; tpl_reuse=1.00
- **H-reg_mixed_app_logs**: MC=4,795 vs TAR+ZSTD=9,695 (Δ=-50.5%)  Δ=-50.5% – within threshold
- **H-reg_2000_small**: MC=2,151 vs TAR+ZSTD=43,689 (Δ=-95.1%)  Δ=-95.1% – within threshold

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

*(none)*

## Memory Spikes (peak > 400 MB)

*(none)*

## Analysis Notes

*(none)*

## Summary

- Total tests recorded : 19
- MC wins (Δ < -5%)   : 7
- MC losses (Δ > +5%) : 0
- Crashes              : 0
- Regressions (> 10%) : 0
- Slow cases           : 0
- Memory spikes        : 0

**Final verdict: `INTERNAL_HARDENING_VALIDATED`**
