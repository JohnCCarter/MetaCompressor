# Adaptive v2.2 vs v2.2+field_aware vs v2.2+string_pattern vs v2.2+pipeline vs v2.2+relational benchmark

Delta is `(compressed_size - tarzstd_size) / tarzstd_size`; negative is better.

## Summary

| Mode | Win-rate vs TAR | Avg delta | Worst loss | Avg build | Avg encode |
| ---- | --------------: | --------: | ---------: | --------: | ---------: |
| `v2.2` | 83.3% | -24.56% | 1.82% | 0.0363s | 0.0082s |
| `v2.2+field_aware` | 83.3% | -29.05% | 1.82% | 0.0413s | 0.0146s |
| `v2.2+string_pattern` | 83.3% | -29.05% | 1.82% | 0.0411s | 0.0143s |
| `v2.2+pipeline` | 83.3% | -29.05% | 1.82% | 0.0452s | 0.0175s |
| `v2.2+relational` | 83.3% | -24.56% | 1.82% | 0.0446s | 0.0166s |

## Per dataset

### unique lines n=35

| Mode | Selected | Delta % | Size |
| ---- | -------- | ------: | ---: |
| `v2.2` | `row_template` | -24.47% | 250 |
| `v2.2+field_aware` | `row_template` | -24.47% | 250 |
| `v2.2+string_pattern` | `row_template` | -24.47% | 250 |
| `v2.2+pipeline` | `row_template` | -24.47% | 250 |
| `v2.2+relational` | `row_template` | -24.47% | 250 |

### structured repeat n=600

| Mode | Selected | Delta % | Size |
| ---- | -------- | ------: | ---: |
| `v2.2` | `row_template` | 1.82% | 112 |
| `v2.2+field_aware` | `row_template` | 1.82% | 112 |
| `v2.2+string_pattern` | `row_template` | 1.82% | 112 |
| `v2.2+pipeline` | `row_template` | 1.82% | 112 |
| `v2.2+relational` | `row_template` | 1.82% | 112 |

### high-cardinality ids n=300

| Mode | Selected | Delta % | Size |
| ---- | -------- | ------: | ---: |
| `v2.2` | `row_template` | -15.35% | 1037 |
| `v2.2+field_aware` | `row_template` | -15.35% | 1037 |
| `v2.2+string_pattern` | `row_template` | -15.35% | 1037 |
| `v2.2+pipeline` | `row_template` | -15.35% | 1037 |
| `v2.2+relational` | `row_template` | -15.35% | 1037 |

### many-small-files n=80

| Mode | Selected | Delta % | Size |
| ---- | -------- | ------: | ---: |
| `v2.2` | `row_template` | -42.12% | 202 |
| `v2.2+field_aware` | `row_template` | -42.12% | 202 |
| `v2.2+string_pattern` | `row_template` | -42.12% | 202 |
| `v2.2+pipeline` | `row_template` | -42.12% | 202 |
| `v2.2+relational` | `row_template` | -42.12% | 202 |

### mixed fields n=300

| Mode | Selected | Delta % | Size |
| ---- | -------- | ------: | ---: |
| `v2.2` | `columnar_encoding_v2` | -54.56% | 927 |
| `v2.2+field_aware` | `field_aware_columnar_v2` | -79.26% | 423 |
| `v2.2+string_pattern` | `string_pattern_encoding_v1` | -79.26% | 423 |
| `v2.2+pipeline` | `pipeline_columnar_v1` | -79.26% | 423 |
| `v2.2+relational` | `columnar_encoding_v2` | -54.56% | 927 |

### timestamp-heavy n=400

| Mode | Selected | Delta % | Size |
| ---- | -------- | ------: | ---: |
| `v2.2` | `raw_tar_zstd` | -12.68% | 310 |
| `v2.2+field_aware` | `field_aware_columnar_v2` | -14.93% | 302 |
| `v2.2+string_pattern` | `string_pattern_encoding_v1` | -14.93% | 302 |
| `v2.2+pipeline` | `pipeline_columnar_v1` | -14.93% | 302 |
| `v2.2+relational` | `raw_tar_zstd` | -12.68% | 310 |
