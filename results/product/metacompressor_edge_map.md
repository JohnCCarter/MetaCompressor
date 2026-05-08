# MetaCompressor Production Edge Map

> Source: `results/product/metacompressor_production_validation.json`.

## Classification rules

- `CONFIRMED_WIN`: MC beats TAR+ZSTD by at least 10%.
- `PARTIAL_WIN`: MC beats TAR+ZSTD, but by less than 10%.
- `LOSS`: MC is larger than TAR+ZSTD or the final selection fell back to raw TAR+ZSTD.

- Confirmed wins: 1
- Partial wins: 4
- Losses: 5

## Dataset map

| Dataset | Type | MC mode used | Delta vs TAR+ZSTD | Classification | Short reason |
|---|---|---|---:|---|---|
| app_service_logs | app/service logs | `corpus_template_columnar_v1` | -0.1% | PARTIAL_WIN | Effectively a tie: all 10 files fell back and there is no reusable columnar structure, so MC only edges ahead slightly. |
| json_ndjson_logs | JSON/NDJSON logs | `corpus_template_columnar_v1` | +0.0% | LOSS | All 8 files fell back with 0.0% reuse and no columns, so MC adds overhead instead of finding structure. |
| nginx_access_logs | nginx/access logs | `raw_tar_zstd` | +0.6% | LOSS | Final selection fell back to raw TAR+ZSTD because template/columnar paths were worse. |
| mixed_microservice_logs | mixed microservice logs | `corpus_template_columnar_v1` | -2.1% | PARTIAL_WIN | Moderate shared structure (21.9% reuse, 9,133 columns) offsets fallback-heavy files enough to beat TAR+ZSTD. |
| high_cardinality_logs | high-cardinality logs | `corpus_template_columnar_v1` | -0.2% | PARTIAL_WIN | Fallback keeps MC near parity, but only one template and zero reusable columns limit the gain. |
| noisy_low_structure_logs | noisy/low-structure logs | `corpus_template_row_v1` | +9.2% | LOSS | Low-structure, high-cardinality payloads leave row-mode template metadata larger than the TAR+ZSTD baseline. |
| binary_compressed_mix | binary/compressed mixed corpus | `corpus_template_columnar_v1` | -0.0% | PARTIAL_WIN | Mostly binary/pre-compressed content forces 11 binary fallbacks, so MC can only squeeze out a negligible edge. |
| large_corpus_128mb | large corpus: 100MB+ | `corpus_template_columnar_v1` | +0.0% | LOSS | Scale does not help when 20 files fall back and reuse stays at 0.0%; MC analysis adds slight overhead. |
| many_small_files_5000 | many-small-files corpus | `corpus_template_columnar_v1` | -42.3% | CONFIRMED_WIN | Perfect cross-file reuse (100.0%) and zero fallback let columnar templating remove small-file/TAR overhead. |
| very_large_corpus_512mb | very large corpus: 500MB+ | `corpus_template_columnar_v1` | +0.3% | LOSS | Huge synthetic corpus still has 0.0% reuse and 32 fallback files, so MC remains slightly larger. |

## Summary

### Common patterns where MC wins

- The only confirmed win is `many_small_files_5000`, where 100.0% template reuse and zero fallback let MC exploit repeated structure across thousands of tiny files.
- Partial wins cluster where MC still finds at least some structural advantage or where fallback prevents a larger regression (`app_service_logs`, `mixed_microservice_logs`, `high_cardinality_logs`, `binary_compressed_mix`).
- Cross-file structure matters more than corpus size; the strongest positive result comes from repeated patterns across many files, not from the largest datasets.

### Common patterns where MC loses

- Losses concentrate in datasets with 0.0% template reuse and zero reusable columns (`json_ndjson_logs`, `large_corpus_128mb`, `very_large_corpus_512mb`).
- `nginx_access_logs` loses because the final chooser had to fall back to raw TAR+ZSTD after template/columnar variants came out worse.
- `noisy_low_structure_logs` loses because weak structure and high-cardinality literals leave template metadata larger than the generic baseline.

### Key bottlenecks

- Reuse collapse: several corpora generate templates, but reuse stays at 0.0%, so MC carries analysis/container cost without enough dedup benefit.
- Fallback-heavy workloads: binary/raw fallback protects correctness, but once many files fall back, MC mostly ties or slightly trails TAR+ZSTD.
- Columnar overhead on weak structure: when columns are numerous but not compressible enough, metadata outweighs the benefit and the raw fallback selector has to bail out.
- Scale without shared structure: bigger corpora (`large_corpus_128mb`, `very_large_corpus_512mb`) do not improve results when the underlying files still do not share reusable patterns.

## Buckets

- `CONFIRMED_WIN`: many_small_files_5000
- `PARTIAL_WIN`: app_service_logs, mixed_microservice_logs, high_cardinality_logs, binary_compressed_mix
- `LOSS`: json_ndjson_logs, nginx_access_logs, noisy_low_structure_logs, large_corpus_128mb, very_large_corpus_512mb

**EDGE_MAP_CREATED**
