# Adaptive v1 vs v2.2 vs v2.2+hybrid benchmark

Each dataset is compressed with `adaptive="v1"`, `adaptive="v2.2"`, and `adaptive="v2.2+hybrid"`. The hybrid mode adds **hybrid_row_columnar_v1** (per-block dense row table vs columnar encoding) to the v2.2 predictive pool with tie-break preference over pure columnar v2 when final `.mck` sizes tie.

Delta is `(compressed_size - tarzstd_size) / tarzstd_size`; negative means smaller than plain TAR+ZSTD.

## Summary

| Mode | Win-rate vs TAR+ZSTD | Avg delta | Worst loss | Avg build time | Avg encode time |
| ---- | -------------------: | --------: | ---------: | -------------: | --------------: |
| `v1` | 80.0% | -26.99% | 1.82% | 0.0524s | 0.0286s |
| `v2.2` | 80.0% | -26.99% | 1.82% | 0.0491s | 0.0150s |
| `v2.2+hybrid` | 80.0% | -26.99% | 1.82% | 0.0473s | 0.0162s |

## Per dataset

| Dataset | Mode | Selected | Delta vs TAR | Hybrid eligible | Overhead vs col |
| ------- | ---- | -------- | -----------: | :--------------- | --------------: |
| unique lines n=35 | `v1` | `row_template` | -24.47% |  |  |
| unique lines n=35 | `v2.2` | `row_template` | -24.47% |  |  |
| unique lines n=35 | `v2.2+hybrid` | `row_template` | -24.47% | False |  |
| structured repeat n=600 | `v1` | `row_template` | 1.82% |  |  |
| structured repeat n=600 | `v2.2` | `row_template` | 1.82% |  |  |
| structured repeat n=600 | `v2.2+hybrid` | `row_template` | 1.82% | True | 11 |
| high-cardinality ids n=300 | `v1` | `row_template` | -15.35% |  |  |
| high-cardinality ids n=300 | `v2.2` | `row_template` | -15.35% |  |  |
| high-cardinality ids n=300 | `v2.2+hybrid` | `row_template` | -15.35% | False |  |
| many-small-files n=80 | `v1` | `row_template` | -42.12% |  |  |
| many-small-files n=80 | `v2.2` | `row_template` | -42.12% |  |  |
| many-small-files n=80 | `v2.2+hybrid` | `row_template` | -42.12% | True | 9 |
| mixed fields n=180 | `v1` | `columnar_encoding_v2` | -54.81% |  |  |
| mixed fields n=180 | `v2.2` | `columnar_encoding_v2` | -54.81% |  |  |
| mixed fields n=180 | `v2.2+hybrid` | `columnar_encoding_v2` | -54.81% | True | 11 |

## Notes

- `v2.2+hybrid` uses the same predictor as `v2.2` and adds one extra encode pass for hybrid_row_columnar_v1 when columnar v2 is built.
- Pool order tie-break among equal-size candidates: row < hybrid < columnar v2 < TAR.
