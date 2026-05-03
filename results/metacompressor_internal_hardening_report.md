# MetaCompressor Internal Hardening Report

**Verdict:** `INTERNAL_HARDENING_VALIDATED`

## Dataset Results

| Dataset | Raw | MC corpus-template | TAR+ZSTD | Delta % | Per-file ZSTD | gzip | brotli | Compress s | Decomp s | Peak MB | Winner | Notes |
|---------|----:|-------------------:|---------:|-------:|-------------:|-----:|-------:|-----------:|---------:|--------:|--------|-------|
| H-50mb_structured | 52,428,800 | 527 | 5,022 | -89.5% | 4,877 | 178,232 | — | 64.655s | 1.491s | 59.8 MB | MC | 50 MB structured log; tpl_reuse=1.00; ratio=0.00001 |

## Where MC Wins

- **H-50mb_structured**: MC=527 vs TAR+ZSTD=5,022 (Δ=-89.5%)  50 MB structured log; tpl_reuse=1.00; ratio=0.00001

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

- H-50mb_structured: compress 64.7s

## Memory Spikes (peak > 400 MB)

*(none)*

## Analysis Notes

*(none)*

## Summary

- Total tests recorded : 1
- MC wins (Δ < -5%)   : 1
- MC losses (Δ > +5%) : 0
- Crashes              : 0
- Regressions (> 10%) : 0
- Slow cases           : 1
- Memory spikes        : 0

**Final verdict: `INTERNAL_HARDENING_VALIDATED`**
