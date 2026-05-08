# Differential Hit-Rate Report

- Verification mode only: `true`
- Cache return enabled: `False`
- Run count per workload: `20`
- Mutating hit-rate avg: `0.412`
- Mutating archives_equal|hit avg: `1.000`
- Phase 3 recommendation: `no_go_keep_verification_mode`

## Workloads

### append-only logs (unchanged)
- run_count: 20
- cache_hit_candidate_rate: 0.950
- archives_equal_rate: 0.950
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: archive_missing
- partial_reuse_opportunity_count: 0
- reusable_but_not_hit_chunks_avg: 0.000
- estimated_benefit_if_partial_reuse_existed: 0.000
- reuse_chunk_ratio_avg: 0.000
- rescan_chunk_ratio_avg: 0.000
- total_time_ms_avg: 4.80
- total_time_ms_stdev: 0.93
- lossless_status: pass
- determinism_status: pass

### append-only logs (append-only)
- run_count: 20
- cache_hit_candidate_rate: 0.450
- archives_equal_rate: 0.450
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: chunk_hash_changed
- partial_reuse_opportunity_count: 10
- reusable_but_not_hit_chunks_avg: 55.455
- estimated_benefit_if_partial_reuse_existed: 55.455
- reuse_chunk_ratio_avg: 0.942
- rescan_chunk_ratio_avg: 0.008
- total_time_ms_avg: 30.25
- total_time_ms_stdev: 2.21
- lossless_status: pass
- determinism_status: pass

### structured corpora (unchanged)
- run_count: 20
- cache_hit_candidate_rate: 0.950
- archives_equal_rate: 0.950
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: archive_missing
- partial_reuse_opportunity_count: 0
- reusable_but_not_hit_chunks_avg: 0.000
- estimated_benefit_if_partial_reuse_existed: 0.000
- reuse_chunk_ratio_avg: 0.000
- rescan_chunk_ratio_avg: 0.000
- total_time_ms_avg: 4.55
- total_time_ms_stdev: 0.80
- lossless_status: pass
- determinism_status: pass

### structured corpora (small-change)
- run_count: 20
- cache_hit_candidate_rate: 0.900
- archives_equal_rate: 0.900
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: archive_missing
- partial_reuse_opportunity_count: 1
- reusable_but_not_hit_chunks_avg: 42.000
- estimated_benefit_if_partial_reuse_existed: 42.000
- reuse_chunk_ratio_avg: 0.949
- rescan_chunk_ratio_avg: 0.001
- total_time_ms_avg: 24.50
- total_time_ms_stdev: 1.86
- lossless_status: pass
- determinism_status: pass

### mixed binaries (unchanged)
- run_count: 20
- cache_hit_candidate_rate: 0.950
- archives_equal_rate: 0.950
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: archive_missing
- partial_reuse_opportunity_count: 0
- reusable_but_not_hit_chunks_avg: 0.000
- estimated_benefit_if_partial_reuse_existed: 0.000
- reuse_chunk_ratio_avg: 0.000
- rescan_chunk_ratio_avg: 0.000
- total_time_ms_avg: 4.65
- total_time_ms_stdev: 0.79
- lossless_status: pass
- determinism_status: pass

### mixed binaries (small-change)
- run_count: 20
- cache_hit_candidate_rate: 0.300
- archives_equal_rate: 0.300
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: chunk_hash_changed
- partial_reuse_opportunity_count: 13
- reusable_but_not_hit_chunks_avg: 137.429
- estimated_benefit_if_partial_reuse_existed: 137.429
- reuse_chunk_ratio_avg: 0.946
- rescan_chunk_ratio_avg: 0.004
- total_time_ms_avg: 25.05
- total_time_ms_stdev: 2.13
- lossless_status: pass
- determinism_status: pass

### noisy datasets (unchanged)
- run_count: 20
- cache_hit_candidate_rate: 0.950
- archives_equal_rate: 0.950
- archives_equal_given_cache_hit_rate: 1.0
- top_miss_reason: archive_missing
- partial_reuse_opportunity_count: 0
- reusable_but_not_hit_chunks_avg: 0.000
- estimated_benefit_if_partial_reuse_existed: 0.000
- reuse_chunk_ratio_avg: 0.000
- rescan_chunk_ratio_avg: 0.000
- total_time_ms_avg: 4.85
- total_time_ms_stdev: 1.15
- lossless_status: pass
- determinism_status: pass

### noisy datasets (noisy-change)
- run_count: 20
- cache_hit_candidate_rate: 0.000
- archives_equal_rate: 0.000
- archives_equal_given_cache_hit_rate: None
- top_miss_reason: chunk_hash_changed
- partial_reuse_opportunity_count: 0
- reusable_but_not_hit_chunks_avg: 0.000
- estimated_benefit_if_partial_reuse_existed: 0.000
- reuse_chunk_ratio_avg: 0.000
- rescan_chunk_ratio_avg: 0.950
- total_time_ms_avg: 37.15
- total_time_ms_stdev: 1.49
- lossless_status: pass
- determinism_status: pass

