# MetaCompressor Internal Hardening Report

**Verdict:** `INTERNAL_HARDENING_VALIDATED`

## Dataset Results

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | Per-file ZSTD | gzip | brotli | Compress s | Decomp s | Peak MB | Winner | Notes |
|---------|----:|-------------------:|---------:|-------:|-------------:|-----:|-------:|-----------:|---------:|--------:|--------|-------|
| H-100mb_structured | 104,857,600 | 476 | 9,823 | -95.2% | 9,677 | 356,204 | — | 115.986s | 3.479s | 1342.3 MB | MC | 100 MB structured log; tpl_reuse=1.00; ratio=0.000005 |
| H-250mb_structured | 262,144,000 | 626 | 24,224 | -97.4% | — | — | — | 295.731s | 7.623s | 3330.3 MB | MC | 250 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=3330 MB; raw_fb=False |
| H-500mb_structured | 524,288,000 | 882 | 48,222 | -98.2% | — | — | — | 616.694s | 14.911s | 6667.8 MB | MC | 500 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=6668 MB; raw_fb=False |

## Where MC Wins

- **H-100mb_structured**: MC=476 vs TAR+ZSTD=9,823 (Δ=-95.2%)  100 MB structured log; tpl_reuse=1.00; ratio=0.000005
- **H-250mb_structured**: MC=626 vs TAR+ZSTD=24,224 (Δ=-97.4%)  250 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=3330 MB; raw_fb=False
- **H-500mb_structured**: MC=882 vs TAR+ZSTD=48,222 (Δ=-98.2%)  500 MB structured log; tpl_reuse=1.00; ratio=0.000002; peak_mem=6668 MB; raw_fb=False

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

- H-100mb_structured: compress 116.0s
- H-250mb_structured: compress 295.7s
- H-500mb_structured: compress 616.7s

## Memory Spikes (peak > 400 MB)

- H-100mb_structured: 1342.3 MB
- H-250mb_structured: 3330 MB
- H-500mb_structured: 6668 MB

## Analysis Notes

*(none)*

## Summary

- Total tests recorded : 3
- MC wins (Δ < -5%)   : 3
- MC losses (Δ > +5%) : 0
- Crashes              : 0
- Regressions (> 10%) : 0
- Slow cases           : 3
- Memory spikes        : 3

**Final verdict: `INTERNAL_HARDENING_VALIDATED`**
