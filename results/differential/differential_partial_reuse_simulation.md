# Differential Partial Reuse Simulation Report

- Simulation only: `true`
- Run count per workload: `20`

## Workloads

### append-only logs
- run_count: 20
- full_rebuild_time_ms_avg: 399.85
- full_rebuild_time_ms_stdev: 147.68
- estimated_partial_reuse_saved_chunks: 58.40
- estimated_partial_reuse_saved_bytes: 9130.01
- estimated_partial_reuse_saved_time_ms: 381.80
- estimated_partial_reuse_build_fraction: 0.058
- estimated_partial_reuse_speedup_pct: 94.19

### structured corpora
- run_count: 20
- full_rebuild_time_ms_avg: 347.20
- full_rebuild_time_ms_stdev: 65.36
- estimated_partial_reuse_saved_chunks: 71.10
- estimated_partial_reuse_saved_bytes: 6650.75
- estimated_partial_reuse_saved_time_ms: 328.65
- estimated_partial_reuse_build_fraction: 0.100
- estimated_partial_reuse_speedup_pct: 90.00

### mixed binaries
- run_count: 20
- full_rebuild_time_ms_avg: 379.10
- full_rebuild_time_ms_stdev: 125.05
- estimated_partial_reuse_saved_chunks: 137.10
- estimated_partial_reuse_saved_bytes: 2941.93
- estimated_partial_reuse_saved_time_ms: 351.45
- estimated_partial_reuse_build_fraction: 0.054
- estimated_partial_reuse_speedup_pct: 94.55

### noisy datasets
- run_count: 20
- full_rebuild_time_ms_avg: 332.40
- full_rebuild_time_ms_stdev: 86.14
- estimated_partial_reuse_saved_chunks: 0.00
- estimated_partial_reuse_saved_bytes: 0.00
- estimated_partial_reuse_saved_time_ms: 0.00
- estimated_partial_reuse_build_fraction: 1.000
- estimated_partial_reuse_speedup_pct: 0.00

