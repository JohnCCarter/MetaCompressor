# Phase 2 ZSTD-Affine Shaping Research (Report-Only)

Scope: report-only research; no wire-format or runtime shaping changes.

## Dataset Summary

- `json_ndjson_logs`: affinity=0.542, encode_ms=9592, decode_ms=210, ratio_vs_tar_zstd=-44.471%
  - candidates: column_locality, delta_friendly_numeric_lanes, dictionary_token_substitution, repeated_structural_markers, stable_field_ordering, template_grouping
  - expected: moderate_affinity_only_selective_shaping_likely_to_help
  - safety gate: insufficient_affinity_for_confident_shaping_without_risk
- `many_small_files_5000`: affinity=0.401, encode_ms=11997, decode_ms=4290, ratio_vs_tar_zstd=-39.9%
  - candidates: dictionary_token_substitution, repeated_structural_markers, stable_field_ordering, template_grouping
  - expected: moderate_affinity_only_selective_shaping_likely_to_help
  - safety gate: insufficient_affinity_for_confident_shaping_without_risk
- `app_service_logs`: affinity=0.400, encode_ms=9424, decode_ms=233, ratio_vs_tar_zstd=-47.786%
  - candidates: dictionary_token_substitution, repeated_structural_markers, stable_field_ordering, template_grouping
  - expected: moderate_affinity_only_selective_shaping_likely_to_help
  - safety gate: insufficient_affinity_for_confident_shaping_without_risk
- `structured_scale_100mb`: affinity=0.400, encode_ms=138326, decode_ms=1996, ratio_vs_tar_zstd=-49.223%
  - candidates: dictionary_token_substitution, repeated_structural_markers, stable_field_ordering, template_grouping
  - expected: moderate_affinity_only_selective_shaping_likely_to_help
  - safety gate: insufficient_affinity_for_confident_shaping_without_risk

## Recommended Phase 3 Candidate

- dataset: `json_ndjson_logs`
- candidate: `column_locality`
- rationale: highest affinity + sizable encode-time budget under current report-only evidence.

## Guardrails

- keep Phase 3 behind feature flag
- no wire-format change
- fail closed on ratio/time regression
- require lossless + deterministic verification
