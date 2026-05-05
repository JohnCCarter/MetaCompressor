# v2.3 generalization validation benchmark suite

Broader validation across heterogeneous corpora to measure generalization (not weight tuning).

## Summary

- Dataset count: **15**
- Win-rate vs TAR+ZSTD: **66.7%**
- Avg delta: **-29.40%**
- Worst loss: **0.65%**
- Avg candidates built: **1.27**
- Avg encode time: **0.1767s**
- Avg decode time: **0.0646s**
- Fallback triggered count: **1**
- Fallback reasons: **{'container_overhead_guard': 1}**

## Per dataset

| Dataset | Profile | Selected mode | Win vs TAR | Delta | Candidate count | Fallback triggered | Fallback reason | Encode time | Decode time |
| ------- | ------- | ------------- | ---------: | ----: | --------------: | -----------------: | -------------- | ----------: | ----------: |
| json_logs_small | `json` | `columnar_encoding_v2` | Y | -53.66% | 1 | N | `None` | 0.0283s | 0.0015s |
| json_logs_large | `json` | `raw_tar_zstd` | N | 0.43% | 0 | N | `None` | 0.1063s | 0.0025s |
| ndjson_app_logs_medium | `logs` | `columnar_encoding_v2` | Y | -23.50% | 3 | N | `None` | 0.1282s | 0.0025s |
| nginx_access_medium | `nginx` | `raw_tar_zstd` | N | 0.65% | 0 | N | `None` | 0.1585s | 0.0020s |
| timestamp_heavy_small | `logs` | `field_aware_columnar_v2` | Y | -60.15% | 3 | N | `None` | 0.0585s | 0.0027s |
| timestamp_heavy_large | `logs` | `columnar_encoding_v2` | Y | -73.71% | 3 | N | `None` | 0.5078s | 0.0048s |
| high_cardinality_ids_medium | `generic` | `columnar_encoding_v2` | Y | -28.28% | 1 | N | `None` | 0.0810s | 0.0033s |
| many_small_files_small | `generic` | `columnar_encoding_v2` | Y | -23.36% | 1 | N | `None` | 0.0570s | 0.1282s |
| many_small_files_large | `generic` | `raw_tar_zstd` | Y | -13.75% | 0 | N | `None` | 0.2348s | 0.8022s |
| random_noise_binary | `generic` | `raw_tar_zstd` | N | 0.02% | 0 | N | `None` | 0.0039s | 0.0056s |
| mixed_structured_logs_medium | `logs` | `field_aware_columnar_v2` | Y | -88.75% | 3 | N | `None` | 0.2439s | 0.0037s |
| semi_structured_messages | `generic` | `field_aware_columnar_v2` | Y | -63.70% | 3 | N | `None` | 0.2576s | 0.0034s |
| small_corpus_mixed | `logs` | `plain_tar_zstd_passthrough` | N | 0.00% | 1 | Y | `container_overhead_guard` | 0.0058s | 0.0026s |
| medium_corpus_mixed | `logs` | `raw_tar_zstd` | Y | -13.38% | 0 | N | `None` | 0.1528s | 0.0016s |
| large_corpus_mixed | `logs` | `raw_tar_zstd` | N | 0.19% | 0 | N | `None` | 0.6262s | 0.0019s |