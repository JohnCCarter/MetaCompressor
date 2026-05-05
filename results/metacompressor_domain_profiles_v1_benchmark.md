# Domain profiles benchmark (adaptive=v2.2+pipeline)

Compare generic vs logs profile and generic vs nginx profile.

## mixed logs n=300

| Profile | Selected mode | Delta % | Size |
| ------- | ------------- | ------: | ---: |
| `generic` | `pipeline_columnar_v1` | -79.26% | 423 |
| `logs` | `pipeline_columnar_v1` | -79.26% | 423 |
| `nginx` | `pipeline_columnar_v1` | -79.26% | 423 |

## nginx-like n=500

| Profile | Selected mode | Delta % | Size |
| ------- | ------------- | ------: | ---: |
| `generic` | `raw_tar_zstd` | 1.51% | 2819 |
| `logs` | `raw_tar_zstd` | 1.51% | 2819 |
| `nginx` | `raw_tar_zstd` | 1.51% | 2819 |
