# MetaCompressor Structure Extraction v2 Report

| Dataset | Before Δ vs TAR+ZSTD | After Δ vs TAR+ZSTD | Reuse Before | Reuse After | Mode | Verdict |
|---|---:|---:|---:|---:|---|---|
| app_service_logs | -3.4% | -47.8% | 3.7% | 100.0% | columnar | strong win |
| json_ndjson_logs | -8.3% | -44.5% | 0.3% | 100.0% | columnar | strong win |
| nginx_access_logs | 1.8% | -72.0% | 21.4% | 100.0% | columnar | strong win |
| mixed_microservice_logs | -5.8% | -45.5% | 23.4% | 100.0% | columnar | strong win |
| high_cardinality_logs | -10.7% | -12.3% | 0.0% | 100.0% | columnar | strong win |
| noisy_low_structure_logs | 0.0% | -40.8% | 75.2% | 100.0% | columnar | strong win |
| binary_compressed_mix | -0.9% | -7.2% | 2.6% | 100.0% | columnar | win |
| large_corpus_128mb | -3.7% | -49.2% | 5.1% | 100.0% | columnar | strong win |
| many_small_files_5000 | -40.4% | -39.9% | 100.0% | 100.0% | columnar | strong win |
