# Phase 5 Product Benchmark Suite

Source: `results/metacompressor_acceptance_hardening.json`

- datasets evaluated: 10
- MC wins vs TAR+ZSTD: 10
- MC losses vs TAR+ZSTD: 0
- near ties (|delta| <= 2%): 0

## Where MC wins

- `nginx_access_logs` (-71.999%)
- `structured_scale_100mb` (-49.223%)
- `structured_scale_50mb` (-49.021%)
- `app_service_logs` (-47.786%)
- `structured_scale_10mb` (-47.454%)
- `mixed_microservice_logs` (-45.475%)
- `json_ndjson_logs` (-44.471%)
- `noisy_low_structure_logs` (-40.833%)
- `many_small_files_5000` (-39.9%)
- `high_cardinality_logs` (-12.334%)

## Where MC loses

- (none)

## Recommendation

- Keep safe fallback behavior for datasets that regress under future workloads.
- Continue widening benchmark diversity (mixed/noisy/high-entropy) to validate adoption guidance.
