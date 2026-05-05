# v2.2 vs v2.3 predictive+profiles benchmark

v2.2 uses current baseline behavior. v2.3 uses predictive-only build (top-1/top-2) with profile-aware ranking.

## Summary

| Mode | Win rate | Avg delta | Worst loss | Avg time |
| ---- | -------: | --------: | ---------: | -------: |
| `v2.2` | 50.0% | -26.52% | 1.51% | 0.0790s |
| `v2.3` | 50.0% | -38.88% | 1.51% | 0.0966s |

- v2.3 top-1 accuracy: **25.0%**
- v2.3 top-2 accuracy: **50.0%**
- v2.3 avg candidates built: **1.50**

## Per dataset

| Dataset | Profile | v2.2 mode | v2.2 delta | v2.2 time | v2.3 mode | v2.3 delta | v2.3 time | Built | Top1 | Top2 | Ranked (v2.3) | Features (v2.3) | Strategy scores (v2.3) |
| ------- | ------- | --------- | ---------: | --------: | --------- | ---------: | --------: | ----: | ----: | ----: | ------------- | --------------- | ---------------------- |
| mixed logs n=300 | `logs` | `columnar_encoding_v2` | -54.56% | 0.0716s | `field_aware_columnar_v2` | -79.26% | 0.0960s | 3 | Y | Y | `['field_aware_columnar_v2', 'pipeline_columnar_v1', 'columnar_encoding_v2', 'string_pattern_encoding_v1', 'relational_encoding_v1', 'row_template']` | `{'token_reuse_ratio': 1.0, 'average_token_length': 3.7579710144927536, 'prefix_similarity_score': 0.0, 'field_variance_score': 1.0}` | `{'row_template': 1.3876198691222694, 'columnar_encoding_v2': 1.0154437893880501, 'field_aware_columnar_v2': 1.0194437893880501, 'string_pattern_encoding_v1': 1.02044378938805, 'pipeline_columnar_v1': 1.0214437893880501, 'relational_encoding_v1': 1.0234437893880501}` |
| mixed logs n=300 | `nginx` | `columnar_encoding_v2` | -54.56% | 0.0637s | `field_aware_columnar_v2` | -79.26% | 0.0911s | 3 | N | Y | `['string_pattern_encoding_v1', 'field_aware_columnar_v2', 'columnar_encoding_v2', 'pipeline_columnar_v1', 'relational_encoding_v1', 'row_template']` | `{'token_reuse_ratio': 1.0, 'average_token_length': 3.7579710144927536, 'prefix_similarity_score': 0.0, 'field_variance_score': 1.0}` | `{'row_template': 1.3876198691222694, 'columnar_encoding_v2': 1.016239999, 'field_aware_columnar_v2': 1.020239999, 'string_pattern_encoding_v1': 1.0212399989999998, 'pipeline_columnar_v1': 1.022239999, 'relational_encoding_v1': 1.024239999}` |
| nginx-like n=500 | `logs` | `raw_tar_zstd` | 1.51% | 0.0932s | `raw_tar_zstd` | 1.51% | 0.1112s | 0 | N | N | `[]` | `{'token_reuse_ratio': 0.9855072463768116, 'average_token_length': 2.773391304347826, 'prefix_similarity_score': 0.0, 'field_variance_score': 0.780502}` | `{}` |
| nginx-like n=500 | `nginx` | `raw_tar_zstd` | 1.51% | 0.0873s | `raw_tar_zstd` | 1.51% | 0.0880s | 0 | N | N | `[]` | `{'token_reuse_ratio': 0.9855072463768116, 'average_token_length': 2.773391304347826, 'prefix_similarity_score': 0.0, 'field_variance_score': 0.780502}` | `{}` |