# MetaCompressor adaptive selection v1

**Before:** pick the smaller of row template vs columnar v2, then swap to TAR+ZSTD-in-MCK if that archive exceeded ``_CORPUS_FALLBACK_THRESHOLD × tarzstd_size``.

**After (v1):** build row, columnar v2, columnar v1, and TAR+MCK; drop row/columnar candidates that fail the same threshold gate vs ``tarzstd_size``; choose the smallest remaining final ``.mck`` with deterministic tie-break (row, then v2, v1, TAR).

Table columns: **TAR+ZSTD** = plain corpus TAR+ZSTD bytes; **Row** / **Columnar** = full ``.mck`` sizes for that encoding; **Selected** = adaptive winner; **Delta vs TAR+ZSTD** = ``compressed_size − tarzstd_size`` (negative means smaller than plain TAR+ZSTD).

| Dataset | TAR+ZSTD | Row | Columnar | Selected | Winner | Delta vs TAR+ZSTD |
| ------- | -------: | --: | -------: | -------- | ------ | ----------------: |
| prefixed NDJSON n=50 | 291 | 297 | 429 | `row_template` | Row template | 6 |
| plain NDJSON n=50 | 272 | 279 | 410 | `row_template` | Row template | 7 |
| plain NDJSON n=500 | 737 | 645 | 757 | `raw_tar_zstd` | TAR+ZSTD (MCK) | -100 |
| mixed microservice-like | 523 | 538 | 482 | `columnar_encoding_v2` | Columnar | -41 |
| nginx-like access n=200 | 708 | 740 | 728 | `columnar_encoding_v1` | Columnar | 20 |
| high-cardinality ids n=150 | 542 | 524 | 729 | `row_template` | Row template | -18 |
| many-small-files n=80 | 533 | 277 | 387 | `row_template` | Row template | -256 |
| structured logs n=200 | 614 | 711 | 276 | `columnar_encoding_v2` | Columnar | -338 |

## Pytest (fast suite)

```text
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 85%]
......................................                                   [100%]
254 passed, 9 deselected in 38.83s
```

```text
ADAPTIVE_SELECTION_V1_VALIDATED
```
