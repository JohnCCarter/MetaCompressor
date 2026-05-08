# MetaCompressor Edge Validation Report

> Generated automatically by the edge-validation benchmark script.
> All sizes in bytes at ZSTD level 3.  MC corpus-template uses extended
> tokeniser (UUID, ISO datetime, IPv4, 0x-hex, URL, number).

## Summary table

| Dataset | Raw bytes | ZSTD/file | TAR+ZSTD | MC corpus | Tpl/file | MC corpus-tpl | Delta (vs TAR+ZSTD) | Winner |
|---------|----------:|----------:|---------:|----------:|---------:|---------------:|---------------------|--------|
| Synthetic app logs | 459,375 | 82,519 | 83,094 | 84,235 | 83,192 | 64,472 | -18,622 (22.4% SMALLER) | **MC corpus-tpl ✓** |
| JSON (NDJSON) logs | 572,995 | 137,073 | 137,613 | 139,362 | 128,131 | 119,275 | -18,338 (13.3% SMALLER) | **MC corpus-tpl ✓** |
| Nginx access-style logs | 562,421 | 124,557 | 123,036 | 124,435 | 124,979 | 108,584 | -14,452 (11.7% SMALLER) | **MC corpus-tpl ✓** |
| Mixed service logs | 639,013 | 127,534 | 136,981 | 138,676 | 126,307 | 117,344 | -19,637 (14.3% SMALLER) | **MC corpus-tpl ✓** |
| Low-structure / noisy logs | 133,180 | 63,269 | 63,454 | 63,967 | 63,558 | 62,477 | -977 (1.5% SMALLER) | **MC corpus-tpl ✓** |

## Per-dataset details

### Synthetic app logs

**Verdict:** MC corpus-template is **18,622 bytes (22.4%) SMALLER** than TAR+ZSTD.

**Sizes:**

- Raw: 459,375 bytes across 20 files
- ZSTD per-file: 82,519 bytes  (2 ms)
- TAR+ZSTD: 83,094 bytes  (2 ms)
- MC corpus: 84,235 bytes  (672 ms)
- Template per-file: 83,192 bytes  (43 ms)
- MC corpus-template: 64,472 bytes  (77 ms)

**Explainability:**

- Files: 20
- Lines: 6,020
- Shared templates: 141
- Template reuse count: 6,020
- Template reuse rate: 100.0%
- Raw fallback lines: 0
- Binary fallback files: 0
- Avg vars/templated line: 3.39

**Timing breakdown:**

- Template extraction: 74.2 ms
- Serialisation: 1.4 ms
- Zstd compression: 1.2 ms
- Total: 77.1 ms

### JSON (NDJSON) logs

**Verdict:** MC corpus-template is **18,338 bytes (13.3%) SMALLER** than TAR+ZSTD.

**Sizes:**

- Raw: 572,995 bytes across 15 files
- ZSTD per-file: 137,073 bytes  (2 ms)
- TAR+ZSTD: 137,613 bytes  (3 ms)
- MC corpus: 139,362 bytes  (1058 ms)
- Template per-file: 128,131 bytes  (43 ms)
- MC corpus-template: 119,275 bytes  (76 ms)

**Explainability:**

- Files: 15
- Lines: 3,000
- Shared templates: 28
- Template reuse count: 3,000
- Template reuse rate: 100.0%
- Raw fallback lines: 0
- Binary fallback files: 0
- Avg vars/templated line: 6.00

**Timing breakdown:**

- Template extraction: 72.2 ms
- Serialisation: 0.9 ms
- Zstd compression: 2.3 ms
- Total: 75.7 ms

### Nginx access-style logs

**Verdict:** MC corpus-template is **14,452 bytes (11.7%) SMALLER** than TAR+ZSTD.

**Sizes:**

- Raw: 562,421 bytes across 10 files
- ZSTD per-file: 124,557 bytes  (2 ms)
- TAR+ZSTD: 123,036 bytes  (3 ms)
- MC corpus: 124,435 bytes  (1026 ms)
- Template per-file: 124,979 bytes  (55 ms)
- MC corpus-template: 108,584 bytes  (96 ms)

**Explainability:**

- Files: 10
- Lines: 4,010
- Shared templates: 246
- Template reuse count: 4,010
- Template reuse rate: 100.0%
- Raw fallback lines: 0
- Binary fallback files: 0
- Avg vars/templated line: 10.45

**Timing breakdown:**

- Template extraction: 92.2 ms
- Serialisation: 1.8 ms
- Zstd compression: 2.2 ms
- Total: 96.5 ms

### Mixed service logs

**Verdict:** MC corpus-template is **19,637 bytes (14.3%) SMALLER** than TAR+ZSTD.

**Sizes:**

- Raw: 639,013 bytes across 28 files
- ZSTD per-file: 127,534 bytes  (3 ms)
- TAR+ZSTD: 136,981 bytes  (3 ms)
- MC corpus: 138,676 bytes  (1038 ms)
- Template per-file: 126,307 bytes  (53 ms)
- MC corpus-template: 117,344 bytes  (93 ms)

**Explainability:**

- Files: 28
- Lines: 7,028
- Shared templates: 22
- Template reuse count: 7,028
- Template reuse rate: 100.0%
- Raw fallback lines: 0
- Binary fallback files: 0
- Avg vars/templated line: 3.69

**Timing breakdown:**

- Template extraction: 88.9 ms
- Serialisation: 1.7 ms
- Zstd compression: 2.4 ms
- Total: 93.3 ms

### Low-structure / noisy logs

**Verdict:** MC corpus-template is **977 bytes (1.5%) SMALLER** than TAR+ZSTD.

**Sizes:**

- Raw: 133,180 bytes across 8 files
- ZSTD per-file: 63,269 bytes  (1 ms)
- TAR+ZSTD: 63,454 bytes  (1 ms)
- MC corpus: 63,967 bytes  (82 ms)
- Template per-file: 63,558 bytes  (15 ms)
- MC corpus-template: 62,477 bytes  (27 ms)

**Explainability:**

- Files: 8
- Lines: 2,408
- Shared templates: 8
- Template reuse count: 1,431
- Template reuse rate: 59.4%
- Raw fallback lines: 977
- Binary fallback files: 0
- Avg vars/templated line: 2.98

**Timing breakdown:**

- Template extraction: 25.5 ms
- Serialisation: 0.5 ms
- Zstd compression: 0.9 ms
- Total: 27.1 ms

## Final verdict

**EDGE_VALIDATED**

MC corpus-template beats TAR+ZSTD on 5/5 datasets:
- Synthetic app logs: 18,622 bytes (22.4%) SMALLER
- JSON (NDJSON) logs: 18,338 bytes (13.3%) SMALLER
- Nginx access-style logs: 14,452 bytes (11.7%) SMALLER
- Mixed service logs: 19,637 bytes (14.3%) SMALLER
- Low-structure / noisy logs: 977 bytes (1.5%) SMALLER

