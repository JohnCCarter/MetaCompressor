# Differential Partial Reuse Simulation Report

- Simulation only: `true`
- Run count per workload: `20`

## Workloads

### append-only logs
- run_count: 20
- full_rebuild_time_ms_avg: 424.35
- full_rebuild_time_ms_stdev: 174.96
- estimated_partial_reuse_saved_chunks: 58.40
- estimated_partial_reuse_saved_bytes: 9130.01
- estimated_partial_reuse_saved_time_ms: 406.30
- estimated_partial_reuse_build_fraction: 0.058
- estimated_partial_reuse_speedup_pct: 94.19

### structured corpora
- run_count: 20
- full_rebuild_time_ms_avg: 497.85
- full_rebuild_time_ms_stdev: 163.64
- estimated_partial_reuse_saved_chunks: 71.10
- estimated_partial_reuse_saved_bytes: 6650.75
- estimated_partial_reuse_saved_time_ms: 467.95
- estimated_partial_reuse_build_fraction: 0.100
- estimated_partial_reuse_speedup_pct: 90.00

### mixed binaries
- run_count: 20
- full_rebuild_time_ms_avg: 305.10
- full_rebuild_time_ms_stdev: 91.09
- estimated_partial_reuse_saved_chunks: 137.10
- estimated_partial_reuse_saved_bytes: 2941.93
- estimated_partial_reuse_saved_time_ms: 285.21
- estimated_partial_reuse_build_fraction: 0.054
- estimated_partial_reuse_speedup_pct: 94.55

### noisy datasets
- run_count: 20
- full_rebuild_time_ms_avg: 461.30
- full_rebuild_time_ms_stdev: 552.96
- estimated_partial_reuse_saved_chunks: 0.00
- estimated_partial_reuse_saved_bytes: 0.00
- estimated_partial_reuse_saved_time_ms: 0.00
- estimated_partial_reuse_build_fraction: 1.000
- estimated_partial_reuse_speedup_pct: 0.00

