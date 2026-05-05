# Adaptive v1 vs v2 benchmark (encode phase)

**encode_s** sums row + columnar (+ v1 columnar in v1 path) serialize/encode timings from metrics. v2 skips some full archive builds when the predictor allows it.

| Dataset | encode_s v1 | encode_s v2 | v2 skipped builds | v1 selected | v2 selected |
| ------- | ----------: | ----------: | :---------------- | ----------- | ----------- |
| unique lines n=35 | 0.0131 | 0.0000 | True | `row_template` | `raw_tar_zstd` |
| structured repeat n=600 | 0.0194 | 0.0010 | False | `row_template` | `row_template` |

## Summary

On the high-entropy micro-corpus, v2 **skipped** full template archive builds (encode_s → 0) and still selected TAR after the usual size gates.  On the structured corpus, v2 built only the predicted template candidate(s), so encode_s stays well below v1 (which always builds row + two columnar passes).
