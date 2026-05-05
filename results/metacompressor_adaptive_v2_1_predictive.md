# Adaptive v1 vs v2 vs v2.1 predictive benchmark

Each dataset is compressed with `adaptive="v1"`, `adaptive="v2"`, and `adaptive="v2.1"`. Delta is `(compressed_size - tarzstd_size) / tarzstd_size`; negative means smaller than plain TAR+ZSTD.

`v2.1` uses `aggression_factor=1.0` for this report.

## Summary

| Mode | Win-rate vs TAR+ZSTD | Avg delta | Worst loss | Avg build time | Avg encode time |
| ---- | -------------------: | --------: | ---------: | -------------: | --------------: |
| `v1` | 80.0% | -26.99% | 1.82% | 0.0291s | 0.0158s |
| `v2` | 20.0% | -3.49% | 12.69% | 0.0275s | 0.0042s |
| `v2.1` | 80.0% | -16.73% | 1.82% | 0.0291s | 0.0060s |

## v2.1 Confidence Buckets

| Bucket | Cases | Metric A | Metric B |
| ------ | ----: | -------: | -------: |
| high_confidence_cases | 3 | avg_delta -14.45% | win_rate 100.0% |
| low_confidence_cases | 2 | fallback_rate 0.0% | worst_loss 1.82% |

## Per Dataset

| Dataset | Mode | Selected | Delta vs TAR | Build time | encode_s | Confidence | Skipped builds | Prediction error |
| ------- | ---- | -------- | -----------: | ---------: | -------: | ---------- | :------------- | ---------------: |
| unique lines n=35 | `v1` | `row_template` | -24.47% | 0.0270s | 0.0131s |  | False |  |
| unique lines n=35 | `v2` | `raw_tar_zstd` | 12.69% | 0.0231s | 0.0000s | unknown | True | 0 |
| unique lines n=35 | `v2.1` | `row_template` | -24.47% | 0.0197s | 0.0040s | high | False | -165 |
| structured repeat n=600 | `v1` | `row_template` | 1.82% | 0.0214s | 0.0184s |  | False |  |
| structured repeat n=600 | `v2` | `row_template` | 1.82% | 0.0261s | 0.0010s | unknown | False | 0 |
| structured repeat n=600 | `v2.1` | `row_template` | 1.82% | 0.0435s | 0.0144s | low | False | 27 |
| high-cardinality ids n=300 | `v1` | `row_template` | -15.35% | 0.0219s | 0.0065s |  | False |  |
| high-cardinality ids n=300 | `v2` | `raw_tar_zstd` | 3.43% | 0.0236s | 0.0000s | unknown | True | 0 |
| high-cardinality ids n=300 | `v2.1` | `row_template` | -15.35% | 0.0239s | 0.0008s | high | False | -562 |
| many-small-files n=80 | `v1` | `row_template` | -42.12% | 0.0558s | 0.0302s |  | False |  |
| many-small-files n=80 | `v2` | `row_template` | -42.12% | 0.0481s | 0.0200s | unknown | False | 0 |
| many-small-files n=80 | `v2.1` | `row_template` | -42.12% | 0.0402s | 0.0099s | risk | False | -42 |
| mixed fields n=180 | `v1` | `columnar_encoding_v2` | -54.81% | 0.0195s | 0.0109s |  | False |  |
| mixed fields n=180 | `v2` | `raw_tar_zstd` | 6.73% | 0.0167s | 0.0000s | unknown | True | 0 |
| mixed fields n=180 | `v2.1` | `row_template` | -3.53% | 0.0182s | 0.0007s | high | False | -223 |

## Notes

- `v1` remains the exhaustive baseline: row + columnar v2 + columnar v1 are built.
- `v2` is the first predictive selector.
- `v2.1` uses explicit `entropy_estimate * size_weight + metadata_overhead_penalty + cardinality_penalty` scores, raw score-gap confidence, and records prediction error.
- `v2.1` confidence-aware aggression: high score-gap builds only the best candidate; mid score-gap builds the top 2; risk cases fall back to TAR/safe mode.
