# Adaptive v1 vs v2/v2.1/v2.2 predictive benchmark

Each dataset is compressed with `adaptive="v1"`, `adaptive="v2"`, `adaptive="v2.1"`, and `adaptive="v2.2"`. Delta is `(compressed_size - tarzstd_size) / tarzstd_size`; negative means smaller than plain TAR+ZSTD.

`v2.1` and `v2.2` use `aggression_factor=1.0` for this report.

## Summary

| Mode | Win-rate vs TAR+ZSTD | Avg delta | Worst loss | Avg build time | Avg encode time |
| ---- | -------------------: | --------: | ---------: | -------------: | --------------: |
| `v1` | 80.0% | -26.99% | 1.82% | 0.0265s | 0.0140s |
| `v2` | 20.0% | -3.49% | 12.69% | 0.0266s | 0.0042s |
| `v2.1` | 80.0% | -16.73% | 1.82% | 0.0278s | 0.0054s |
| `v2.2` | 80.0% | -26.99% | 1.82% | 0.0334s | 0.0086s |

## Confidence Buckets

| Mode | Bucket | Cases | Metric A | Metric B |
| ---- | ------ | ----: | -------: | -------: |
| `v2.1` | high_confidence_cases | 3 | avg_delta -14.45% | win_rate 100.0% |
| `v2.1` | low_confidence_cases | 2 | fallback_rate 0.0% | worst_loss 1.82% |
| `v2.2` | high_confidence_cases | 2 | avg_delta -19.91% | win_rate 100.0% |
| `v2.2` | low_confidence_cases | 3 | fallback_rate 0.0% | worst_loss 1.82% |

## Per Dataset

| Dataset | Mode | Selected | Delta vs TAR | Build time | encode_s | Confidence | Model quality | Structure score | Strong structure | Skipped builds | Prediction error |
| ------- | ---- | -------- | -----------: | ---------: | -------: | ---------- | ------------: | --------------: | :--------------- | :------------- | ---------------: |
| unique lines n=35 | `v1` | `row_template` | -24.47% | 0.0216s | 0.0117s |  |  |  | None | False |  |
| unique lines n=35 | `v2` | `raw_tar_zstd` | 12.69% | 0.0184s | 0.0000s | unknown | 1.000 | 0.000 | False | True | 0 |
| unique lines n=35 | `v2.1` | `row_template` | -24.47% | 0.0196s | 0.0034s | high | 1.000 | 0.000 | False | False | -165 |
| unique lines n=35 | `v2.2` | `row_template` | -24.47% | 0.0186s | 0.0034s | high | 0.818 | 0.000 | False | False | -165 |
| structured repeat n=600 | `v1` | `row_template` | 1.82% | 0.0194s | 0.0173s |  |  |  | None | False |  |
| structured repeat n=600 | `v2` | `row_template` | 1.82% | 0.0286s | 0.0010s | unknown | 1.000 | 0.000 | False | False | 0 |
| structured repeat n=600 | `v2.1` | `row_template` | 1.82% | 0.0359s | 0.0112s | low | 1.000 | 0.000 | False | False | 27 |
| structured repeat n=600 | `v2.2` | `row_template` | 1.82% | 0.0389s | 0.0136s | low | 1.000 | 0.000 | True | False | 27 |
| high-cardinality ids n=300 | `v1` | `row_template` | -15.35% | 0.0210s | 0.0058s |  |  |  | None | False |  |
| high-cardinality ids n=300 | `v2` | `raw_tar_zstd` | 3.43% | 0.0213s | 0.0000s | unknown | 1.000 | 0.000 | False | True | 0 |
| high-cardinality ids n=300 | `v2.1` | `row_template` | -15.35% | 0.0242s | 0.0009s | high | 1.000 | 0.000 | False | False | -562 |
| high-cardinality ids n=300 | `v2.2` | `row_template` | -15.35% | 0.0220s | 0.0015s | high | 0.980 | 0.000 | True | False | -562 |
| many-small-files n=80 | `v1` | `row_template` | -42.12% | 0.0505s | 0.0273s |  |  |  | None | False |  |
| many-small-files n=80 | `v2` | `row_template` | -42.12% | 0.0473s | 0.0203s | unknown | 1.000 | 0.000 | False | False | 0 |
| many-small-files n=80 | `v2.1` | `row_template` | -42.12% | 0.0384s | 0.0107s | risk | 1.000 | 0.000 | False | False | -42 |
| many-small-files n=80 | `v2.2` | `row_template` | -42.12% | 0.0500s | 0.0169s | low | 0.874 | 0.000 | True | False | -42 |
| mixed fields n=180 | `v1` | `columnar_encoding_v2` | -54.81% | 0.0200s | 0.0081s |  |  |  | None | False |  |
| mixed fields n=180 | `v2` | `raw_tar_zstd` | 6.73% | 0.0174s | 0.0000s | unknown | 1.000 | 0.000 | False | True | 0 |
| mixed fields n=180 | `v2.1` | `row_template` | -3.53% | 0.0207s | 0.0008s | high | 1.000 | 0.000 | False | False | -223 |
| mixed fields n=180 | `v2.2` | `columnar_encoding_v2` | -54.81% | 0.0372s | 0.0077s | low | 0.985 | 0.000 | True | False | -352 |

## Notes

- `v1` remains the exhaustive baseline: row + columnar v2 + columnar v1 are built.
- `v2` is the first predictive selector.
- `v2.1` uses explicit `entropy_estimate * size_weight + metadata_overhead_penalty + cardinality_penalty` scores, raw score-gap confidence, and records prediction error.
- `v2.1` confidence-aware aggression: high score-gap builds only the best candidate; mid score-gap builds the top 2; risk cases fall back to TAR/safe mode.
- `v2.2` adds deterministic structure-score sampling, a stable-structure columnar boost, and separate `prediction_confidence` vs `model_quality` metrics.
