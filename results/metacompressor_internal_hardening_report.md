# MetaCompressor Internal Hardening Report

**Verdict:** `INTERNAL_HARDENING_VALIDATED`

## Dataset Results

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | Per-file ZSTD | gzip | brotli | Compress s | Decomp s | Peak MB | Winner | Notes |
|---------|----:|-------------------:|---------:|-------:|-------------:|-----:|-------:|-----------:|---------:|--------:|--------|-------|
| H-50mb_structured | 52,428,800 | 2,617 | 5,023 | -47.9% | 4,877 | 178,231 | — | 8.669s | 1.244s | 301.3 MB | MC | 50 MB structured log; tpl_reuse=1.00; ratio=0.00005 |
| H-100mb_structured | 104,857,600 | 5,032 | 9,821 | -48.8% | 9,677 | 356,204 | — | 17.289s | 2.484s | 602.9 MB | MC | 100 MB structured log; tpl_reuse=1.00; ratio=0.000048 |
| H-2000_small_files | 152,679 | 6,495 | 39,587 | -83.6% | 145,326 | 54,562 | — | 0.736s | 0.170s | 6.8 MB | MC | 2 000 small files; tpl_reuse=1.00; files=2000 |
| H-mixed_app_logs | 146,392 | 8,756 | 9,672 | -9.5% | 7,679 | 13,971 | — | 0.014s | 0.003s | — | per-file-zstd | 4 app log formats; tpl_reuse=1.00; templates=9 |
| H-nginx_10k | 1,183,385 | 121,514 | 117,210 | 3.7% | 117,045 | 156,900 | — | 0.108s | 0.023s | — | per-file-zstd | 10 000 nginx lines; tpl_reuse=1.00; templates=1; low_struct_fb=0 |
| H-ndjson_50k | 5,346,556 | 167,755 | 225,618 | -25.6% | 225,197 | 436,341 | — | 1.948s | 0.084s | 61.5 MB | MC | 50k NDJSON lines; tpl_reuse=1.00; templates=3 |
| H-prose | 184,915 | 40,655 | 40,808 | -0.4% | 40,592 | 35,501 | — | — | — | — | gzip | prose text; tpl_reuse=0.00; binary_fb=1; low_struct_fb=0 |
| H-low_struct_fallback | — | — | — | — | — | — | — | — | — | — | — | low-structure fallback fires; low_struct_fb=1 |
| H-low_struct_size | — | 469 | 592 | -20.8% | 394 | — | — | — | — | — | per-file-zstd | low-struct size test; Δ=-20.8%; low_struct_fb=1 |
| H-highcard_2k | 170,690 | 43,519 | 43,550 | -0.1% | 43,381 | 44,976 | — | — | — | — | per-file-zstd | 2000 lines; recurring tpl; random vals; tpl_reuse=0.00; Δ=-0.1% |
| H-random_binary_mix | 470,304 | 99,340 | 98,830 | 0.5% | 98,485 | — | — | — | — | — | per-file-zstd | random+structured mix; binary_fb=2; tpl_reuse=1.00 |
| H-precompressed_mix | 40,177 | 304 | 446 | -31.8% | 249 | — | — | — | — | — | per-file-zstd | gz+zst+log; binary_fb=2; tpl_reuse=1.00 |
| H-binary_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 20 random-binary files; all must round-trip without corruption |
| H-hybrid_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 50 unique-template lines → hybrid fallback → lossless |
| H-low_struct_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | ~3% template rate → low-structure fallback; low_struct_fb=1 |
| H-no_silent_corruption_large | — | — | — | — | — | — | — | — | — | — | — | corrupt archive raised exception (correct) |
| H-reg_structured_50mb | — | 2,617 | 5,023 | -47.9% | — | — | — | — | — | — | MC | Δ=-47.9% – within threshold |
| H-reg_mixed_app_logs | — | 8,756 | 9,675 | -9.5% | — | — | — | — | — | — | MC | Δ=-9.5% – within threshold |
| H-reg_2000_small | — | 6,495 | 39,263 | -83.5% | — | — | — | — | — | — | MC | Δ=-83.5% – within threshold |
| H-reg_prose | — | 40,655 | 40,807 | -0.4% | — | — | — | — | — | — | MC | Δ=-0.4% – within threshold |
| H-reg_random_binary | — | 65,603 | 65,733 | -0.2% | — | — | — | — | — | — | MC | Δ=-0.2% – within threshold |
| H-determinism_10mb | — | 663 | — | — | — | — | — | — | — | — | MC | two independent compressions of identical 10 MB corpus → identical bytes |
| H-determinism_200files | — | 967 | — | — | — | — | — | — | — | — | MC | 200 small files, two runs → identical bytes |

## Where MC Wins

- **H-50mb_structured**: MC=2,617 vs TAR+ZSTD=5,023 (Δ=-47.9%)  50 MB structured log; tpl_reuse=1.00; ratio=0.00005
- **H-100mb_structured**: MC=5,032 vs TAR+ZSTD=9,821 (Δ=-48.8%)  100 MB structured log; tpl_reuse=1.00; ratio=0.000048
- **H-2000_small_files**: MC=6,495 vs TAR+ZSTD=39,587 (Δ=-83.6%)  2 000 small files; tpl_reuse=1.00; files=2000
- **H-mixed_app_logs**: MC=8,756 vs TAR+ZSTD=9,672 (Δ=-9.5%)  4 app log formats; tpl_reuse=1.00; templates=9
- **H-ndjson_50k**: MC=167,755 vs TAR+ZSTD=225,618 (Δ=-25.6%)  50k NDJSON lines; tpl_reuse=1.00; templates=3
- **H-low_struct_size**: MC=469 vs TAR+ZSTD=592 (Δ=-20.8%)  low-struct size test; Δ=-20.8%; low_struct_fb=1
- **H-precompressed_mix**: MC=304 vs TAR+ZSTD=446 (Δ=-31.8%)  gz+zst+log; binary_fb=2; tpl_reuse=1.00
- **H-reg_structured_50mb**: MC=2,617 vs TAR+ZSTD=5,023 (Δ=-47.9%)  Δ=-47.9% – within threshold
- **H-reg_mixed_app_logs**: MC=8,756 vs TAR+ZSTD=9,675 (Δ=-9.5%)  Δ=-9.5% – within threshold
- **H-reg_2000_small**: MC=6,495 vs TAR+ZSTD=39,263 (Δ=-83.5%)  Δ=-83.5% – within threshold

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

- H-100mb_structured: 602.9 MB

## Analysis Notes

**H-nginx_10k**: MC is 3.7% larger than TAR+ZSTD. Nginx logs have many variable slots per line (IP, timestamp, path, status, size, latency) with high cardinality. The per-record msgpack overhead and unique variable values outweigh the template saving.

## Summary

- Total tests recorded : 23
- MC wins (Δ < -5%)   : 10
- MC losses (Δ > +5%) : 0
- Crashes              : 0
- Regressions (> 10%) : 0
- Slow cases           : 0
- Memory spikes        : 1

**Final verdict: `INTERNAL_HARDENING_VALIDATED`**
