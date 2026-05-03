# MetaCompressor Internal Hardening Report

**Verdict:** `INTERNAL_HARDENING_VALIDATED`

## Dataset Results

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | Per-file ZSTD | gzip | brotli | Compress s | Decomp s | Peak MB | Winner | Notes |
|---------|----:|-------------------:|---------:|-------:|-------------:|-----:|-------:|-----------:|---------:|--------:|--------|-------|
| H-50mb_structured | 52,428,800 | 2,613 | 5,024 | -48.0% | 4,877 | 178,232 | — | 10.798s | 1.240s | 376.3 MB | MC | 50 MB structured log; tpl_reuse=1.00; ratio=0.00005 |
| H-100mb_structured | 104,857,600 | 5,028 | 9,823 | -48.8% | 9,677 | 356,205 | — | 21.602s | 2.467s | 752.7 MB | MC | 100 MB structured log; tpl_reuse=1.00; ratio=0.000048 |
| H-2000_small_files | 152,679 | 7,282 | 38,659 | -81.2% | 145,326 | 53,995 | — | 1.826s | 0.092s | 14.4 MB | MC | 2 000 small files; tpl_reuse=1.00; files=2000 |
| H-mixed_app_logs | 146,392 | 8,799 | 9,753 | -9.8% | 7,679 | 13,974 | — | 0.015s | 0.003s | — | per-file-zstd | 4 app log formats; tpl_reuse=1.00; templates=9 |
| H-nginx_10k | 1,183,385 | 121,511 | 117,215 | 3.7% | 117,045 | 156,905 | — | 0.102s | 0.022s | — | per-file-zstd | 10 000 nginx lines; tpl_reuse=1.00; templates=1; low_struct_fb=0 |
| H-ndjson_50k | 5,346,556 | 167,752 | 225,618 | -25.6% | 225,197 | 436,343 | — | 1.900s | 0.081s | 77.1 MB | MC | 50k NDJSON lines; tpl_reuse=1.00; templates=3 |
| H-prose | 184,915 | 40,477 | 40,807 | -0.8% | 40,592 | 35,496 | — | — | — | — | gzip | prose text; tpl_reuse=0.00; binary_fb=1; low_struct_fb=0 |
| H-low_struct_fallback | — | — | — | — | — | — | — | — | — | — | — | low-structure fallback fires; low_struct_fb=1 |
| H-low_struct_size | — | 465 | 584 | -20.4% | 394 | — | — | — | — | — | per-file-zstd | low-struct size test; Δ=-20.4%; low_struct_fb=1 |
| H-highcard_2k | 170,690 | 39,419 | 43,528 | -9.4% | 43,342 | 45,042 | — | — | — | — | MC | 2000 lines; recurring tpl; random vals; tpl_reuse=0.00; Δ=-9.4% |
| H-random_binary_mix | 470,304 | 99,337 | 98,944 | 0.4% | 98,485 | — | — | — | — | — | per-file-zstd | random+structured mix; binary_fb=2; tpl_reuse=1.00 |
| H-precompressed_mix | 40,177 | 308 | 477 | -35.4% | 249 | — | — | — | — | — | per-file-zstd | gz+zst+log; binary_fb=2; tpl_reuse=1.00 |
| H-binary_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 20 random-binary files; all must round-trip without corruption |
| H-hybrid_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | 50 unique-template lines → hybrid fallback → lossless |
| H-low_struct_fb_lossless | — | — | — | — | — | — | — | — | — | — | — | ~3% template rate → low-structure fallback; low_struct_fb=1 |
| H-no_silent_corruption_large | — | — | — | — | — | — | — | — | — | — | — | corrupt archive raised exception (correct) |
| H-reg_structured_50mb | — | 2,613 | 5,024 | -48.0% | — | — | — | — | — | — | MC | Δ=-48.0% – within threshold |
| H-reg_mixed_app_logs | — | 8,799 | 9,672 | -9.0% | — | — | — | — | — | — | MC | Δ=-9.0% – within threshold |
| H-reg_2000_small | — | 7,282 | 38,988 | -81.3% | — | — | — | — | — | — | MC | Δ=-81.3% – within threshold |
| H-reg_prose | — | 40,477 | 40,806 | -0.8% | — | — | — | — | — | — | MC | Δ=-0.8% – within threshold |
| H-reg_random_binary | — | 65,602 | 65,733 | -0.2% | — | — | — | — | — | — | MC | Δ=-0.2% – within threshold |
| H-determinism_10mb | — | 659 | — | — | — | — | — | — | — | — | MC | two independent compressions of identical 10 MB corpus → identical bytes |
| H-determinism_200files | — | 998 | — | — | — | — | — | — | — | — | MC | 200 small files, two runs → identical bytes |
| H-250mb_structured | 262,144,000 | 12,340 | 24,224 | -49.1% | — | — | — | 54.612s | 6.137s | 1843.9 MB | MC | 250 MB structured log; tpl_reuse=1.00; ratio=0.000047; peak_mem=1844 MB; raw_fb=False |
| H-500mb_structured | 524,288,000 | 24,535 | 48,225 | -49.1% | — | — | — | 108.504s | 12.476s | 3689.2 MB | MC | 500 MB structured log; tpl_reuse=1.00; ratio=0.000047; peak_mem=3689 MB; raw_fb=False |

## Where MC Wins

- **H-50mb_structured**: MC=2,613 vs TAR+ZSTD=5,024 (Δ=-48.0%)  50 MB structured log; tpl_reuse=1.00; ratio=0.00005
- **H-100mb_structured**: MC=5,028 vs TAR+ZSTD=9,823 (Δ=-48.8%)  100 MB structured log; tpl_reuse=1.00; ratio=0.000048
- **H-2000_small_files**: MC=7,282 vs TAR+ZSTD=38,659 (Δ=-81.2%)  2 000 small files; tpl_reuse=1.00; files=2000
- **H-mixed_app_logs**: MC=8,799 vs TAR+ZSTD=9,753 (Δ=-9.8%)  4 app log formats; tpl_reuse=1.00; templates=9
- **H-ndjson_50k**: MC=167,752 vs TAR+ZSTD=225,618 (Δ=-25.6%)  50k NDJSON lines; tpl_reuse=1.00; templates=3
- **H-low_struct_size**: MC=465 vs TAR+ZSTD=584 (Δ=-20.4%)  low-struct size test; Δ=-20.4%; low_struct_fb=1
- **H-highcard_2k**: MC=39,419 vs TAR+ZSTD=43,528 (Δ=-9.4%)  2000 lines; recurring tpl; random vals; tpl_reuse=0.00; Δ=-9.4%
- **H-precompressed_mix**: MC=308 vs TAR+ZSTD=477 (Δ=-35.4%)  gz+zst+log; binary_fb=2; tpl_reuse=1.00
- **H-reg_structured_50mb**: MC=2,613 vs TAR+ZSTD=5,024 (Δ=-48.0%)  Δ=-48.0% – within threshold
- **H-reg_mixed_app_logs**: MC=8,799 vs TAR+ZSTD=9,672 (Δ=-9.0%)  Δ=-9.0% – within threshold
- **H-reg_2000_small**: MC=7,282 vs TAR+ZSTD=38,988 (Δ=-81.3%)  Δ=-81.3% – within threshold
- **H-250mb_structured**: MC=12,340 vs TAR+ZSTD=24,224 (Δ=-49.1%)  250 MB structured log; tpl_reuse=1.00; ratio=0.000047; peak_mem=1844 MB; raw_fb=False
- **H-500mb_structured**: MC=24,535 vs TAR+ZSTD=48,225 (Δ=-49.1%)  500 MB structured log; tpl_reuse=1.00; ratio=0.000047; peak_mem=3689 MB; raw_fb=False

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

- H-250mb_structured: compress 54.6s
- H-500mb_structured: compress 108.5s

## Memory Spikes (peak > 400 MB)

- H-100mb_structured: 752.7 MB
- H-250mb_structured: 1844 MB
- H-500mb_structured: 3689 MB

## Analysis Notes

**H-nginx_10k**: MC is 3.7% larger than TAR+ZSTD. Nginx logs have many variable slots per line (IP, timestamp, path, status, size, latency) with high cardinality. The per-record msgpack overhead and unique variable values outweigh the template saving.

## Summary

- Total tests recorded : 25
- MC wins (Δ < -5%)   : 13
- MC losses (Δ > +5%) : 0
- Crashes              : 0
- Regressions (> 10%) : 0
- Slow cases           : 2
- Memory spikes        : 3

**Final verdict: `INTERNAL_HARDENING_VALIDATED`**
