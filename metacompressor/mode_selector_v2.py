"""Predictive corpus-template mode selection (Adaptive v2).

Deterministic lightweight sampling plus pass-1 aggregates estimate whether
row template, columnar v2, or TAR+ZSTD-in-MCK is preferable *before* building
all full template archives.  When confidence is high and TAR is predicted,
row/columnar/v1 builds are skipped.  Otherwise at most one or two template
candidates are built, then compared to TAR with the usual fallback threshold
and a strict post-build tolerance vs plain TAR+ZSTD size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Tuple

from metacompressor.corpus_template import (
    _MIN_TEMPLATE_OCCURRENCES,
    _analyze_line,
    _iter_text_lines,
    _LineAnalysis,
    _normalized_skeleton,
    _tokenize_legacy,
)

PrimaryBuild = Literal["row_template", "columnar_encoding_v2", "raw_tar_zstd"]


@dataclass(frozen=True)
class PredictorConfigV2:
    max_sample_lines: int = 4000
    max_sample_files: int = 32
    safety_margin_ratio: float = 1.02
    tolerance_vs_tar: float = 1.02
    confidence_verify_threshold: float = 0.68
    score_confidence_threshold: float = 0.035
    high_confidence_score_gap: float = 0.080
    low_confidence_score_gap: float = 0.025
    aggression_factor: float = 1.0
    columnar_aggression_confidence: float = 0.80
    skip_tar_guard_confidence: float = 0.90
    tar_skip_builds_confidence: float = 0.78
    max_cardinality_track: int = 512
    expected_size_weight: float = 1.0
    structure_weight: float = 0.12
    structure_signal_threshold: float = 0.82
    structure_override_margin: float = 0.075
    model_quality_threshold: float = 0.72
    model_quality_min_sample_lines: int = 64


@dataclass
class CorpusSampleFeatures:
    sample_lines: int = 0
    unique_lines_in_sample: int = 0
    binary_files_seen: int = 0
    text_files_sampled: int = 0
    total_files: int = 0
    total_raw_bytes: int = 0
    files_under_4k: int = 0
    files_over_1m: int = 0
    mean_line_len: float = 0.0
    line_len_variance: float = 0.0
    json_lines_in_sample: int = 0
    json_dominant_key_share: float = 0.0
    max_line_repeat_fraction: float = 0.0
    mean_slot_cardinality_ratio: float = 0.0
    dominant_skeleton_share: float = 0.0
    structure_score: float = 1.0
    structure_stability: float = 0.0
    structure_unique_key_sets: int = 0
    structure_unique_key_set_ratio: float = 1.0
    structure_dominant_key_set_share: float = 0.0
    structure_keyed_line_fraction: float = 0.0


@dataclass
class Pass1QuickStats:
    total_lines: int
    num_text_files: int
    num_binary_files: int
    num_shared_templates: int
    shared_template_line_fraction: float
    avg_var_slots: float
    json_line_fraction: float


@dataclass
class ModePredictionV2:
    primary_build: PrimaryBuild
    verify_second_template: bool
    confidence: float
    scores: Dict[str, float]
    predicted_sizes: Dict[str, int] = field(default_factory=dict)
    score_components: Dict[str, Dict[str, float]] = field(default_factory=dict)
    build_candidates: List[PrimaryBuild] = field(default_factory=list)
    confidence_band: str = "unknown"
    skip_tar_guard: bool = False
    prediction_confidence: float = 0.0
    model_quality: float = 1.0
    reasoning: Dict[str, Any] = field(default_factory=dict)


def _welford_update(
    n: int,
    mean: float,
    m2: float,
    x: float,
) -> Tuple[int, float, float]:
    n1 = n + 1
    delta = x - mean
    mean1 = mean + delta / n1
    delta2 = x - mean1
    m2_1 = m2 + delta * delta2
    return n1, mean1, m2_1


def _line_analysis(line: str, structure_v2_enabled: bool) -> _LineAnalysis:
    if structure_v2_enabled:
        return _analyze_line(line)
    legacy = _tokenize_legacy(line)
    tkey, vals = legacy
    kinds = tuple("legacy" for _ in vals)
    sk = _normalized_skeleton(tkey, kinds, ())
    return _LineAnalysis(
        template_parts=tkey,
        values=list(vals),
        normalized_skeleton=sk,
        value_kinds=kinds,
        is_json=False,
        json_structure_key=(),
    )


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _structure_key_set(analysis: _LineAnalysis) -> Tuple[Tuple[str, ...], bool]:
    """Return a deterministic per-line structural key set.

    JSON and key=value logs use semantic keys.  For less structured lines, fall
    back to the normalized skeleton so the signal stays conservative.
    """
    if analysis.is_json:
        keys = []
        for bit in analysis.json_structure_key:
            key = bit.rsplit("=", 1)[0]
            keys.append("json:%s" % key)
        if keys:
            return tuple(sorted(set(keys))), True

    kv_keys = [
        "kv:%s" % kind[3:] for kind in analysis.value_kinds if kind.startswith("kv:")
    ]
    if kv_keys:
        return tuple(sorted(set(kv_keys))), True

    skeleton = ("skeleton",) + analysis.normalized_skeleton
    return skeleton, False


def sample_corpus_features(
    input_dir: Path,
    all_files: List[Path],
    *,
    structure_v2_enabled: bool,
    config: PredictorConfigV2,
) -> CorpusSampleFeatures:
    out = CorpusSampleFeatures()
    out.total_files = len(all_files)
    line_budget = config.max_sample_lines
    files_budget = config.max_sample_files

    line_counts: Dict[str, int] = {}
    skeleton_counts: Dict[Tuple[str, ...], int] = {}
    json_key_counts: Dict[Tuple[str, ...], int] = {}
    structure_key_set_counts: Dict[Tuple[str, ...], int] = {}
    slot_uniques: Dict[Tuple[Tuple[str, ...], int], set] = {}
    unique_line_set: set[str] = set()
    keyed_structure_lines = 0

    n_len = 0
    mean_len = 0.0
    m2_len = 0.0

    text_files_started = 0
    for file_path in all_files:
        if out.sample_lines >= line_budget:
            break
        if text_files_started >= files_budget and out.sample_lines > 0:
            break
        try:
            st = file_path.stat()
        except OSError:
            continue
        out.total_raw_bytes += int(st.st_size)
        if st.st_size < 4096:
            out.files_under_4k += 1
        if st.st_size > 1_000_000:
            out.files_over_1m += 1

        this_file_started = False
        try:
            for line in _iter_text_lines(file_path):
                if line == "":
                    continue
                if out.sample_lines >= line_budget:
                    break
                if not this_file_started:
                    this_file_started = True
                    text_files_started += 1
                    out.text_files_sampled += 1
                out.sample_lines += 1
                unique_line_set.add(line)
                line_counts[line] = line_counts.get(line, 0) + 1
                n_len, mean_len, m2_len = _welford_update(
                    n_len, mean_len, m2_len, float(len(line))
                )

                analysis = _line_analysis(line, structure_v2_enabled)
                sk = analysis.normalized_skeleton
                skeleton_counts[sk] = skeleton_counts.get(sk, 0) + 1
                structure_keys, has_semantic_keys = _structure_key_set(analysis)
                structure_key_set_counts[structure_keys] = (
                    structure_key_set_counts.get(structure_keys, 0) + 1
                )
                if has_semantic_keys:
                    keyed_structure_lines += 1
                if analysis.is_json:
                    out.json_lines_in_sample += 1
                    jk = analysis.json_structure_key
                    json_key_counts[jk] = json_key_counts.get(jk, 0) + 1
                tpl_key = analysis.template_parts
                for si, val in enumerate(analysis.values):
                    skey = (tpl_key, si)
                    if skey not in slot_uniques:
                        slot_uniques[skey] = set()
                    sset = slot_uniques[skey]
                    if len(sset) < config.max_cardinality_track:
                        sset.add(val)
        except (UnicodeDecodeError, OSError):
            out.binary_files_seen += 1
            continue

    out.unique_lines_in_sample = len(unique_line_set)
    if out.sample_lines > 0:
        out.max_line_repeat_fraction = max(line_counts.values()) / out.sample_lines
        dom_skel = max(skeleton_counts.values()) if skeleton_counts else 0
        out.dominant_skeleton_share = dom_skel / out.sample_lines
        dom_keys = (
            max(structure_key_set_counts.values()) if structure_key_set_counts else 0
        )
        out.structure_unique_key_sets = len(structure_key_set_counts)
        out.structure_dominant_key_set_share = dom_keys / out.sample_lines
        out.structure_keyed_line_fraction = keyed_structure_lines / out.sample_lines
        out.structure_unique_key_set_ratio = (
            out.structure_unique_key_sets / out.sample_lines
        )
        out.structure_score = 1.0 - out.structure_dominant_key_set_share
        out.structure_stability = 1.0 - out.structure_score
    if n_len > 0:
        out.mean_line_len = mean_len
        out.line_len_variance = m2_len / n_len if n_len > 1 else 0.0
    if out.json_lines_in_sample > 0 and json_key_counts:
        out.json_dominant_key_share = (
            max(json_key_counts.values()) / out.json_lines_in_sample
        )

    cap = min(config.max_cardinality_track, max(1, out.sample_lines))
    ratios = [len(s) / cap for s in slot_uniques.values()]
    out.mean_slot_cardinality_ratio = sum(ratios) / len(ratios) if ratios else 0.0

    return out


def compute_pass1_quick_stats(
    tpl_count: Dict[Tuple[str, ...], int],
    tok_cache: Dict[str, _LineAnalysis],
    total_lines: int,
    file_meta: List[Tuple[str, bool]],
    num_shared_templates: int,
) -> Pass1QuickStats:
    num_bin = sum(1 for _r, b in file_meta if b)
    num_txt = len(file_meta) - num_bin
    shared_lines = sum(
        c for _t, c in tpl_count.items() if c >= _MIN_TEMPLATE_OCCURRENCES
    )
    frac = shared_lines / total_lines if total_lines else 0.0

    num = 0
    den = 0
    for tkey, c in tpl_count.items():
        if c < _MIN_TEMPLATE_OCCURRENCES:
            continue
        nvars = 0
        for _line, a in tok_cache.items():
            if a.template_parts == tkey:
                nvars = len(a.values)
                break
        num += nvars * c
        den += c
    avg_vars = num / den if den else 0.0

    json_lines = sum(1 for a in tok_cache.values() if a.is_json)
    json_frac = json_lines / max(1, len(tok_cache))

    return Pass1QuickStats(
        total_lines=total_lines,
        num_text_files=num_txt,
        num_binary_files=num_bin,
        num_shared_templates=num_shared_templates,
        shared_template_line_fraction=frac,
        avg_var_slots=avg_vars,
        json_line_fraction=json_frac,
    )


def _estimate_size_ratios(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    config: PredictorConfigV2,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    unique_ratio = (
        sample.unique_lines_in_sample / sample.sample_lines
        if sample.sample_lines
        else 1.0
    )
    # Do not treat a shared skeleton as reuse when almost every raw line is unique.
    sk_effective = sample.dominant_skeleton_share * max(0.0, 1.0 - unique_ratio)
    reuse_pass1 = max(
        pass1.shared_template_line_fraction,
        sk_effective * 0.88,
    )
    # High unique-line ratio in the sample means template reuse is not compressing
    # distinct payloads, even if a shared skeleton exists in pass-1 counts.
    reuse = min(1.0, max(0.0, reuse_pass1 * max(0.12, 1.0 - 0.92 * unique_ratio)))
    card = min(1.0, sample.mean_slot_cardinality_ratio)
    line_var = min(
        1.0,
        sample.line_len_variance / max(1.0, (sample.mean_line_len + 1.0) ** 2),
    )

    many_small_bias = sample.files_under_4k / max(1, sample.total_files)
    few_large_bias = sample.files_over_1m / max(1, sample.total_files)

    row_ratio = (
        1.02
        - 0.42 * reuse
        + 0.28 * unique_ratio
        + 0.06 * pass1.avg_var_slots
        + 0.05 * line_var
        - 0.04 * many_small_bias
        + 0.12 * few_large_bias
    )

    col_ratio = (
        1.04
        - 0.38 * reuse
        + 0.22 * unique_ratio
        + 0.45 * card
        + 0.08 * pass1.avg_var_slots
        + 0.06 * line_var
        - 0.03
        * sample.json_dominant_key_share
        * (sample.json_lines_in_sample / max(1, sample.sample_lines))
        - 0.03 * many_small_bias
        + 0.10 * few_large_bias
    )

    tar_ratio = 1.0

    if card > 0.55:
        col_ratio += 0.08 * (card - 0.55)

    if reuse < 0.08:
        row_ratio += 0.12
        col_ratio += 0.14

    reasoning: Dict[str, Any] = {
        "reuse_effective": reuse,
        "sample_unique_line_ratio": unique_ratio,
        "mean_slot_cardinality_ratio": card,
        "line_len_variance_norm": line_var,
        "many_small_file_share": many_small_bias,
        "few_large_file_share": few_large_bias,
        "pass1_shared_template_line_fraction": pass1.shared_template_line_fraction,
        "pass1_avg_var_slots": pass1.avg_var_slots,
    }

    scores = {
        "row_template": row_ratio,
        "columnar_encoding_v2": col_ratio,
        "raw_tar_zstd": tar_ratio,
    }
    return scores, reasoning


def _estimate_expected_compression_scores_v21(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    config: PredictorConfigV2,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]], Dict[str, Any]]:
    """Return v2.1 expected compression scores and per-mode score components.

    Lower is better.  Each mode follows the requested component form:

    ``entropy_estimate * size_weight + metadata_overhead_penalty + cardinality_penalty``.
    """
    unique_ratio = (
        sample.unique_lines_in_sample / sample.sample_lines
        if sample.sample_lines
        else 1.0
    )
    sk_effective = sample.dominant_skeleton_share * max(0.0, 1.0 - unique_ratio)
    reuse_pass1 = max(pass1.shared_template_line_fraction, sk_effective * 0.88)
    reuse = min(1.0, max(0.0, reuse_pass1 * max(0.12, 1.0 - 0.92 * unique_ratio)))
    card = min(1.0, sample.mean_slot_cardinality_ratio)
    line_var = min(
        1.0,
        sample.line_len_variance / max(1.0, (sample.mean_line_len + 1.0) ** 2),
    )
    many_small_bias = sample.files_under_4k / max(1, sample.total_files)
    few_large_bias = sample.files_over_1m / max(1, sample.total_files)

    entropy = {
        "row_template": 1.0 - 0.34 * reuse + 0.22 * unique_ratio + 0.04 * line_var,
        "columnar_encoding_v2": (
            1.0 - 0.40 * reuse + 0.17 * unique_ratio + 0.04 * line_var
        ),
        "raw_tar_zstd": 1.0,
    }
    metadata = {
        "row_template": (0.025 + 0.030 * pass1.avg_var_slots - 0.050 * many_small_bias),
        "columnar_encoding_v2": (
            0.065 + 0.050 * pass1.avg_var_slots - 0.035 * many_small_bias
        ),
        "raw_tar_zstd": 0.010 + 0.025 * many_small_bias + 0.015 * few_large_bias,
    }
    cardinality = {
        "row_template": 0.080 * card,
        "columnar_encoding_v2": 0.360 * card,
        "raw_tar_zstd": 0.015 * card,
    }

    components: Dict[str, Dict[str, float]] = {}
    scores: Dict[str, float] = {}
    for mode in ("row_template", "columnar_encoding_v2", "raw_tar_zstd"):
        components[mode] = {
            "entropy_estimate": entropy[mode],
            "size_weight": config.expected_size_weight,
            "metadata_overhead_penalty": metadata[mode],
            "cardinality_penalty": cardinality[mode],
        }
        scores[mode] = (
            entropy[mode] * config.expected_size_weight
            + metadata[mode]
            + cardinality[mode]
        )

    if reuse < 0.08:
        scores["row_template"] += 0.10
        scores["columnar_encoding_v2"] += 0.12
        components["row_template"]["low_reuse_penalty"] = 0.10
        components["columnar_encoding_v2"]["low_reuse_penalty"] = 0.12

    reasoning: Dict[str, Any] = {
        "reuse_effective": reuse,
        "sample_unique_line_ratio": unique_ratio,
        "mean_slot_cardinality_ratio": card,
        "line_len_variance_norm": line_var,
        "many_small_file_share": many_small_bias,
        "few_large_file_share": few_large_bias,
        "pass1_shared_template_line_fraction": pass1.shared_template_line_fraction,
        "pass1_avg_var_slots": pass1.avg_var_slots,
    }
    return scores, components, reasoning


def _estimate_expected_compression_scores_v22(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    config: PredictorConfigV2,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]], Dict[str, Any]]:
    """Return v2.2 scores with a stable-structure columnar boost."""
    scores, components, reasoning = _estimate_expected_compression_scores_v21(
        sample, pass1, config
    )
    scores = dict(scores)
    components = {mode: dict(parts) for mode, parts in components.items()}

    structure_score = _clamp01(sample.structure_score)
    structure_stability = 1.0 - structure_score
    cardinality_signal = _clamp01(sample.mean_slot_cardinality_ratio * 4.0)
    semantic_share = _clamp01(sample.structure_keyed_line_fraction)
    # Skeleton-only structure is useful but weaker than explicit JSON/key=value keys.
    semantic_multiplier = 0.35 + (0.65 * semantic_share)
    structure_boost = (
        structure_stability
        * semantic_multiplier
        * cardinality_signal
        * config.structure_weight
    )
    structured_slot_relief = (
        structure_stability
        * semantic_multiplier
        * cardinality_signal
        * min(
            0.45,
            (0.055 * pass1.avg_var_slots)
            + (0.28 * min(1.0, sample.mean_slot_cardinality_ratio)),
        )
    )

    scores["columnar_encoding_v2"] -= structure_boost + structured_slot_relief
    components["columnar_encoding_v2"]["structure_stability_boost"] = -structure_boost
    components["columnar_encoding_v2"][
        "structured_slot_relief"
    ] = -structured_slot_relief
    components["columnar_encoding_v2"]["structure_score"] = structure_score
    components["columnar_encoding_v2"]["structure_stability"] = structure_stability

    reasoning.update(
        {
            "structure_score": structure_score,
            "structure_stability": structure_stability,
            "structure_unique_key_sets": sample.structure_unique_key_sets,
            "structure_unique_key_set_ratio": sample.structure_unique_key_set_ratio,
            "structure_dominant_key_set_share": sample.structure_dominant_key_set_share,
            "structure_keyed_line_fraction": sample.structure_keyed_line_fraction,
            "structure_cardinality_signal": cardinality_signal,
            "structure_semantic_multiplier": semantic_multiplier,
            "structure_columnar_score_boost": structure_boost,
            "structured_slot_relief": structured_slot_relief,
            "structure_weight": config.structure_weight,
        }
    )
    return scores, components, reasoning


def _estimate_model_quality_v22(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    config: PredictorConfigV2,
) -> Tuple[float, Dict[str, float]]:
    """Estimate whether predictive signals are strong enough to trust."""
    sample_quality = _clamp01(
        sample.sample_lines / max(1, config.model_quality_min_sample_lines)
    )
    structure_stability = _clamp01(sample.structure_stability)
    reuse_consistency = 1.0 - _clamp01(
        abs(pass1.shared_template_line_fraction - sample.dominant_skeleton_share)
    )
    cardinality_consistency = 1.0 - _clamp01(sample.mean_slot_cardinality_ratio * 0.25)
    semantic_quality = 0.60 + (0.40 * _clamp01(sample.structure_keyed_line_fraction))

    model_quality = _clamp01(
        0.25 * sample_quality
        + 0.30 * structure_stability
        + 0.25 * reuse_consistency
        + 0.15 * cardinality_consistency
        + 0.05 * semantic_quality
    )
    parts = {
        "sample_quality": sample_quality,
        "structure_stability": structure_stability,
        "reuse_consistency": reuse_consistency,
        "cardinality_consistency": cardinality_consistency,
        "semantic_quality": semantic_quality,
    }
    return model_quality, parts


def predict_mode_v2(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    tarzstd_size: int,
    config: PredictorConfigV2,
) -> ModePredictionV2:
    del tarzstd_size  # reserved for future calibration against measured tar
    scores, reasoning = _estimate_size_ratios(sample, pass1, config)
    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    best_name, best_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else best_score + 1.0

    margin = config.safety_margin_ratio
    if best_score >= margin - 1e-12:
        primary: PrimaryBuild = "raw_tar_zstd"
    elif best_name == "row_template":
        primary = "row_template"
    elif best_name == "columnar_encoding_v2":
        primary = "columnar_encoding_v2"
    else:
        primary = "raw_tar_zstd"

    gap = second_score - best_score
    confidence = min(1.0, max(0.0, 0.35 + 2.8 * gap))
    if primary == "raw_tar_zstd":
        confidence = min(1.0, confidence + 0.1)

    verify = (
        confidence < config.confidence_verify_threshold and primary != "raw_tar_zstd"
    )

    reasoning["ordered_estimates"] = list(ordered)
    reasoning["safety_margin_ratio"] = margin
    reasoning["confidence_gap"] = gap

    return ModePredictionV2(
        primary_build=primary,
        verify_second_template=verify,
        confidence=confidence,
        scores=scores,
        prediction_confidence=gap,
        reasoning=reasoning,
    )


def predict_mode_v21(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    tarzstd_size: int,
    config: PredictorConfigV2,
) -> ModePredictionV2:
    """Predict using v2.1 component scores and raw score-gap confidence."""
    scores, components, reasoning = _estimate_expected_compression_scores_v21(
        sample, pass1, config
    )
    aggression = max(0.25, config.aggression_factor)
    high_confidence = config.high_confidence_score_gap / aggression
    low_confidence = config.low_confidence_score_gap / aggression

    initial_ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    initial_best_name, initial_best_score = initial_ordered[0]
    initial_second_score = (
        initial_ordered[1][1] if len(initial_ordered) > 1 else initial_best_score
    )
    initial_gap = abs(initial_second_score - initial_best_score)
    aggression_confidence = min(1.0, initial_gap / max(high_confidence, 1e-12))

    allow_columnar_even_if_penalized = False
    columnar_entropy = components["columnar_encoding_v2"]["entropy_estimate"]
    best_entropy = components[initial_best_name]["entropy_estimate"]
    columnar_close = scores["columnar_encoding_v2"] <= initial_best_score + (
        0.045 * aggression
    )
    if (
        initial_best_name != "columnar_encoding_v2"
        and columnar_entropy < best_entropy
        and columnar_close
        and aggression_confidence > config.columnar_aggression_confidence
    ):
        scores = dict(scores)
        scores["columnar_encoding_v2"] = initial_best_score - 1e-9
        allow_columnar_even_if_penalized = True

    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    best_name, best_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else best_score
    score_gap = abs(second_score - best_score)
    confidence = score_gap
    aggression_confidence = min(1.0, score_gap / max(high_confidence, 1e-12))

    skip_tar_guard = (
        aggression_confidence > config.skip_tar_guard_confidence
        and best_name != "raw_tar_zstd"
        and aggression >= 1.0
    )

    if best_score >= config.safety_margin_ratio - 1e-12 and not skip_tar_guard:
        primary: PrimaryBuild = "raw_tar_zstd"
    elif best_name == "row_template":
        primary = "row_template"
    elif best_name == "columnar_encoding_v2":
        primary = "columnar_encoding_v2"
    else:
        primary = "raw_tar_zstd"

    if score_gap > high_confidence:
        confidence_band = "high"
        build_two = False
        fallback_to_safe = False
    elif score_gap > low_confidence:
        confidence_band = "low"
        build_two = True
        fallback_to_safe = False
    else:
        confidence_band = "risk"
        build_two = False
        fallback_to_safe = True
        primary = "raw_tar_zstd"

    build_candidates: List[PrimaryBuild] = []
    if fallback_to_safe:
        build_candidates = []
    elif not (primary == "raw_tar_zstd" and not build_two):
        for mode, _score in ordered:
            candidate = mode  # type: ignore[assignment]
            if candidate == "raw_tar_zstd":
                continue
            build_candidates.append(candidate)  # type: ignore[arg-type]
            if not build_two:
                break
            if len(build_candidates) == 2:
                break

    predicted_sizes = {
        mode: max(1, int(round(score * tarzstd_size))) for mode, score in scores.items()
    }

    reasoning["ordered_initial_scores"] = list(initial_ordered)
    reasoning["ordered_expected_scores"] = list(ordered)
    reasoning["score_gap"] = score_gap
    reasoning["high_confidence_score_gap"] = high_confidence
    reasoning["low_confidence_score_gap"] = low_confidence
    reasoning["aggression_factor"] = config.aggression_factor
    reasoning["aggression_confidence"] = aggression_confidence
    reasoning["allow_columnar_even_if_penalized"] = allow_columnar_even_if_penalized
    reasoning["skip_tar_guard"] = skip_tar_guard
    reasoning["build_two_candidates"] = build_two
    reasoning["fallback_to_tar_or_safe_mode"] = fallback_to_safe

    return ModePredictionV2(
        primary_build=primary,
        verify_second_template=build_two,
        confidence=confidence,
        scores=scores,
        predicted_sizes=predicted_sizes,
        score_components=components,
        build_candidates=build_candidates,
        confidence_band=confidence_band,
        skip_tar_guard=skip_tar_guard,
        prediction_confidence=score_gap,
        model_quality=1.0,
        reasoning=reasoning,
    )


def predict_mode_v22(
    sample: CorpusSampleFeatures,
    pass1: Pass1QuickStats,
    tarzstd_size: int,
    config: PredictorConfigV2,
) -> ModePredictionV2:
    """Predict using v2.2 structure-aware scores and separated confidence signals."""
    raw_scores, _raw_components, raw_reasoning = (
        _estimate_expected_compression_scores_v21(sample, pass1, config)
    )
    scores, components, reasoning = _estimate_expected_compression_scores_v22(
        sample, pass1, config
    )
    model_quality, model_quality_parts = _estimate_model_quality_v22(
        sample, pass1, config
    )

    aggression = max(0.25, config.aggression_factor)
    high_confidence = config.high_confidence_score_gap / aggression
    low_confidence = config.low_confidence_score_gap / aggression

    raw_ordered = sorted(raw_scores.items(), key=lambda kv: (kv[1], kv[0]))
    _raw_best_name, raw_best_score = raw_ordered[0]
    raw_columnar_score = raw_scores["columnar_encoding_v2"]
    structure_stability = reasoning["structure_stability"]
    structure_signal_strong = (
        sample.sample_lines >= max(8, config.model_quality_min_sample_lines // 2)
        and structure_stability >= config.structure_signal_threshold
        and sample.structure_keyed_line_fraction >= 0.50
        and model_quality
        >= max(0.55, config.model_quality_threshold / min(1.5, aggression))
    )

    initial_ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    initial_best_name, initial_best_score = initial_ordered[0]
    allow_columnar_structure_override = False
    raw_columnar_close = raw_columnar_score <= raw_best_score + (
        config.structure_override_margin * aggression * max(0.50, model_quality)
    )
    structure_adjusted_columnar_close = scores[
        "columnar_encoding_v2"
    ] <= initial_best_score + (
        config.structure_override_margin * aggression * max(0.50, model_quality)
    )
    if (
        structure_signal_strong
        and initial_best_name != "columnar_encoding_v2"
        and (raw_columnar_close or structure_adjusted_columnar_close)
    ):
        scores = dict(scores)
        scores["columnar_encoding_v2"] = initial_best_score - 1e-9
        allow_columnar_structure_override = True

    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    best_name, best_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else best_score
    prediction_confidence = abs(second_score - best_score)
    aggression_confidence = min(
        1.0, prediction_confidence / max(high_confidence, 1e-12)
    )

    skip_tar_guard = (
        aggression_confidence > config.skip_tar_guard_confidence
        and model_quality >= config.model_quality_threshold
        and best_name != "raw_tar_zstd"
        and aggression >= 1.0
    )

    if best_score >= config.safety_margin_ratio - 1e-12 and not skip_tar_guard:
        primary: PrimaryBuild = "raw_tar_zstd"
    elif best_name == "row_template":
        primary = "row_template"
    elif best_name == "columnar_encoding_v2":
        primary = "columnar_encoding_v2"
    else:
        primary = "raw_tar_zstd"

    quality_high = model_quality >= config.model_quality_threshold
    quality_low = model_quality >= max(0.50, config.model_quality_threshold * 0.75)
    if prediction_confidence > high_confidence and quality_high:
        confidence_band = "high"
        build_two = False
        fallback_to_safe = False
    elif prediction_confidence > low_confidence and quality_low:
        confidence_band = "low"
        build_two = True
        fallback_to_safe = False
    elif structure_signal_strong and primary == "columnar_encoding_v2":
        confidence_band = "low"
        build_two = True
        fallback_to_safe = False
    else:
        confidence_band = "risk"
        build_two = False
        fallback_to_safe = True
        primary = "raw_tar_zstd"

    build_candidates: List[PrimaryBuild] = []
    if fallback_to_safe:
        build_candidates = []
    elif not (primary == "raw_tar_zstd" and not build_two):
        for mode, _score in ordered:
            candidate = mode  # type: ignore[assignment]
            if candidate == "raw_tar_zstd":
                continue
            build_candidates.append(candidate)  # type: ignore[arg-type]
            if not build_two:
                break
            if len(build_candidates) == 2:
                break

    predicted_sizes = {
        mode: max(1, int(round(score * tarzstd_size))) for mode, score in scores.items()
    }

    reasoning["ordered_raw_v21_scores"] = list(raw_ordered)
    reasoning["ordered_initial_structure_scores"] = list(initial_ordered)
    reasoning["ordered_expected_scores"] = list(ordered)
    reasoning["score_gap"] = prediction_confidence
    reasoning["prediction_confidence"] = prediction_confidence
    reasoning["model_quality"] = model_quality
    reasoning["model_quality_components"] = model_quality_parts
    reasoning["high_confidence_score_gap"] = high_confidence
    reasoning["low_confidence_score_gap"] = low_confidence
    reasoning["aggression_factor"] = config.aggression_factor
    reasoning["aggression_confidence"] = aggression_confidence
    reasoning["structure_signal_strong"] = structure_signal_strong
    reasoning["raw_columnar_close_for_override"] = raw_columnar_close
    reasoning["structure_adjusted_columnar_close_for_override"] = (
        structure_adjusted_columnar_close
    )
    reasoning["allow_columnar_structure_override"] = allow_columnar_structure_override
    reasoning["skip_tar_guard"] = skip_tar_guard
    reasoning["build_two_candidates"] = build_two
    reasoning["fallback_to_tar_or_safe_mode"] = fallback_to_safe
    reasoning["raw_v21_reasoning"] = raw_reasoning

    return ModePredictionV2(
        primary_build=primary,
        verify_second_template=build_two,
        confidence=prediction_confidence,
        scores=scores,
        predicted_sizes=predicted_sizes,
        score_components=components,
        build_candidates=build_candidates,
        confidence_band=confidence_band,
        skip_tar_guard=skip_tar_guard,
        prediction_confidence=prediction_confidence,
        model_quality=model_quality,
        reasoning=reasoning,
    )


def should_skip_template_builds(
    prediction: ModePredictionV2,
    sample: CorpusSampleFeatures,
    config: PredictorConfigV2,
) -> bool:
    """If True, emit TAR+MCK immediately after pass 1 (no row/columnar archives)."""
    if prediction.primary_build != "raw_tar_zstd":
        return False
    if prediction.confidence < config.tar_skip_builds_confidence:
        return False
    if sample.sample_lines < 8:
        return False
    unique_ratio = sample.unique_lines_in_sample / max(1, sample.sample_lines)
    if unique_ratio < 0.93:
        return False
    if sample.max_line_repeat_fraction > 0.08:
        return False
    return True
