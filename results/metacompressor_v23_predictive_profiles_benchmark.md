# v2.2 vs v2.3 predictive+profiles benchmark

v2.2 uses current baseline behavior. v2.3 uses predictive-only build (top-1/top-2) with profile-aware ranking.

## Summary

| Mode | Win rate | Avg delta | Worst loss | Avg time |
| ---- | -------: | --------: | ---------: | -------: |
| `v2.2` | 50.0% | -26.52% | 1.51% | 0.1091s |
| `v2.3` | 50.0% | -38.88% | 1.51% | 0.1179s |

## Per dataset

| Dataset | Profile | v2.2 mode | v2.2 delta | v2.2 time | v2.3 mode | v2.3 delta | v2.3 time | Ranked (v2.3) |
| ------- | ------- | --------- | ---------: | --------: | --------- | ---------: | --------: | ------------- |
| mixed logs n=300 | `logs` | `columnar_encoding_v2` | -54.56% | 0.0892s | `field_aware_columnar_v2` | -79.26% | 0.1193s | `['field_aware_columnar_v2', 'pipeline_columnar_v1', 'columnar_encoding_v2', 'string_pattern_encoding_v1', 'relational_encoding_v1', 'row_template']` |
| mixed logs n=300 | `nginx` | `columnar_encoding_v2` | -54.56% | 0.0788s | `field_aware_columnar_v2` | -79.26% | 0.1123s | `['string_pattern_encoding_v1', 'field_aware_columnar_v2', 'columnar_encoding_v2', 'pipeline_columnar_v1', 'relational_encoding_v1', 'row_template']` |
| nginx-like n=500 | `logs` | `raw_tar_zstd` | 1.51% | 0.1276s | `raw_tar_zstd` | 1.51% | 0.1163s | `[]` |
| nginx-like n=500 | `nginx` | `raw_tar_zstd` | 1.51% | 0.1406s | `raw_tar_zstd` | 1.51% | 0.1238s | `[]` |