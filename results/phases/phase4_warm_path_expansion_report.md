# Phase 4 Warm-Path / Decision Reuse Expansion Report

Scope: report-only evidence refresh; no artifact substitution.

## Scenario Results

- `cold_first_run`: skip_eligible=False, skip_used=False, denied_reason=`stale_or_missing_receipt`, warm_path_used=False, total_quick_time_ms=150627
- `warm_unchanged`: skip_eligible=True, skip_used=True, denied_reason=``, warm_path_used=True, total_quick_time_ms=147819
- `changed_dataset`: skip_eligible=False, skip_used=False, denied_reason=`stale_or_missing_receipt`, warm_path_used=False, total_quick_time_ms=148792
- `rebaseline_after_change`: skip_eligible=False, skip_used=False, denied_reason=`stale_or_missing_receipt`, warm_path_used=False, total_quick_time_ms=143221
- `stale_receipt`: skip_eligible=False, skip_used=False, denied_reason=`stale_or_missing_receipt`, warm_path_used=False, total_quick_time_ms=162957
- `low_confidence_receipt`: skip_eligible=False, skip_used=False, denied_reason=`low_confidence`, warm_path_used=False, total_quick_time_ms=200733
- `receipt_metadata_mismatch`: skip_eligible=False, skip_used=False, denied_reason=`receipt_metadata_mismatch`, warm_path_used=False, total_quick_time_ms=187361

## Recommendation

- Expand only receipt+manifest-integrity-matched warm-path eligibility.
- Keep fail-closed for stale receipt, low confidence, and metadata mismatch states.
- Continue no artifact substitution policy.
