# Differential Partial Reuse Simulation Report

- Simulation only: `true`
- Run count per workload: `20`

## Workloads

### append-only logs
- run_count: 20
- full_rebuild_time_ms_avg: 442.95
- full_rebuild_time_ms_stdev: 48.69
- estimated_partial_reuse_saved_chunks: 58.40
- estimated_partial_reuse_saved_bytes: 9130.01
- estimated_partial_reuse_saved_time_ms: 416.60
- estimated_partial_reuse_build_fraction: 0.058
- estimated_partial_reuse_speedup_pct: 94.19

### structured corpora
- run_count: 20
- full_rebuild_time_ms_avg: 217.10
- full_rebuild_time_ms_stdev: 16.71
- estimated_partial_reuse_saved_chunks: 71.10
- estimated_partial_reuse_saved_bytes: 6650.75
- estimated_partial_reuse_saved_time_ms: 194.85
- estimated_partial_reuse_build_fraction: 0.100
- estimated_partial_reuse_speedup_pct: 90.00

### mixed binaries
- run_count: 20
- full_rebuild_time_ms_avg: 236.00
- full_rebuild_time_ms_stdev: 12.84
- estimated_partial_reuse_saved_chunks: 137.10
- estimated_partial_reuse_saved_bytes: 2941.93
- estimated_partial_reuse_saved_time_ms: 223.25
- estimated_partial_reuse_build_fraction: 0.054
- estimated_partial_reuse_speedup_pct: 94.55

### noisy datasets
- run_count: 20
- full_rebuild_time_ms_avg: 621.65
- full_rebuild_time_ms_stdev: 109.89
- estimated_partial_reuse_saved_chunks: 0.00
- estimated_partial_reuse_saved_bytes: 0.00
- estimated_partial_reuse_saved_time_ms: 0.00
- estimated_partial_reuse_build_fraction: 1.000
- estimated_partial_reuse_speedup_pct: 0.00

