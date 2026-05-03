from __future__ import annotations

from copy import deepcopy
from math import ceil
from typing import Any


def _base_policy() -> dict[str, Any]:
    return {
        "profile": "standard",
        "cross_video_reuse": {
            "enabled": True,
            "target_reuse_ratio": 0.30,
            "max_overlap_with_previous_video": 0.20,
            "max_overlap_with_any_same_channel_video": 0.30,
            "max_overlap_with_other_owned_channels": 0.15,
            "min_days_before_reuse_same_channel": 21,
            "min_videos_before_reuse_same_channel": 10,
            "max_uses_same_channel_lifetime": 4,
            "max_uses_global_lifetime": 8,
            "max_uses_global_30_days": 2,
        },
        "internal_extension": {
            "enabled": True,
            "strategy": "duplicate_with_crossfade",
            "target_extended_track_ratio": 0.35,
            "hard_max_extended_track_ratio": 0.50,
            "preferred_extension_factor": 1.85,
            "max_extension_factor": 2.0,
            "min_crossfade_seconds": 12,
            "preferred_crossfade_seconds": 18,
            "max_crossfade_seconds": 30,
            "max_extended_duration_seconds": 360,
            "max_number_of_extended_clips": 6,
            "max_looped_minutes_ratio": 0.35,
            "hard_max_looped_minutes_ratio": 0.45,
            "require_micro_variation": True,
            "allow_consecutive_extended_clips": False,
        },
        "intro_outro": {
            "first_track_must_be_new": True,
            "second_track_must_be_new": True,
            "last_track_must_be_new": True,
            "first_minutes_must_be_new": 6,
            "last_minutes_must_be_new": 3,
            "forbid_extension_in_first_minutes": 6,
            "forbid_extension_in_last_minutes": 3,
        },
        "sequence": {
            "forbid_repeated_ordered_bigrams": True,
            "forbid_repeated_ordered_trigrams": True,
            "max_longest_common_subsequence_ratio": 0.20,
            "max_same_position_reuse_ratio": 0.10,
        },
        "position": {
            "enabled": True,
            "slot_seconds": 180,
            "forbid_same_slot_reuse": True,
            "forbid_neighbor_slot_reuse_within_days": 30,
        },
        "eligibility": {
            "reusable_only_if": {
                "reusable": True,
                "hook_strength_max": "low",
                "vocal_presence_allowed": False,
                "strong_drop_allowed": False,
                "strong_intro_allowed": False,
                "strong_outro_allowed": False,
                "final_cadence_allowed": False,
            },
            "loopable_only_if": {
                "loop_safe": True,
                "hook_strength_max": "low",
                "vocal_presence_allowed": False,
                "strong_drop_allowed": False,
                "strong_intro_allowed": False,
                "strong_outro_allowed": False,
                "final_cadence_allowed": False,
            },
        },
        "publish_gate": {
            "max_audio_overlap_score": 0.30,
            "max_sequence_similarity_score": 0.20,
            "max_position_similarity_score": 0.10,
            "max_metadata_similarity_score": 0.55,
            "max_visual_similarity_score": 0.45,
            "max_total_repetition_risk_score": 0.35,
        },
        "on_gate_fail": {
            "max_retries": 5,
            "actions": ["reshuffle", "replace_reused_tracks", "regenerate_missing_tracks", "change_energy_curve"],
        },
    }


PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "conservative": {
        "profile": "conservative",
        "cross_video_reuse": {
            "target_reuse_ratio": 0.20,
        },
        "internal_extension": {
            "target_extended_track_ratio": 0.25,
            "hard_max_extended_track_ratio": 0.35,
            "max_looped_minutes_ratio": 0.25,
            "hard_max_looped_minutes_ratio": 0.35,
        },
    },
    "standard": {
        "profile": "standard",
        "cross_video_reuse": {
            "target_reuse_ratio": 0.30,
        },
        "internal_extension": {
            "target_extended_track_ratio": 0.35,
            "hard_max_extended_track_ratio": 0.50,
            "max_looped_minutes_ratio": 0.35,
            "hard_max_looped_minutes_ratio": 0.45,
        },
    },
    "aggressive": {
        "profile": "aggressive",
        "cross_video_reuse": {
            "target_reuse_ratio": 0.40,
        },
        "internal_extension": {
            "target_extended_track_ratio": 0.45,
            "hard_max_extended_track_ratio": 0.60,
            "max_looped_minutes_ratio": 0.45,
            "hard_max_looped_minutes_ratio": 0.55,
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def strategy_preset(profile: str = "standard") -> dict[str, Any]:
    profile = (profile or "standard").strip().lower()
    if profile not in PROFILE_OVERRIDES:
        profile = "standard"
    return deep_merge(_base_policy(), PROFILE_OVERRIDES[profile])


def resolve_asset_strategy(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw or {}
    profile = str(raw.get("profile") or "standard").lower()
    base = strategy_preset(profile if profile in PROFILE_OVERRIDES else "standard")
    resolved = deep_merge(base, raw)
    if "min_fresh_generated_ratio" not in raw and "min_fresh_generated_minutes" in raw:
        resolved["min_fresh_generated_ratio"] = ratio(raw.get("min_fresh_generated_minutes"), 30) / 60
    elif "min_fresh_generated_ratio" not in raw:
        reuse_ratio = ratio(resolved.get("cross_video_reuse", {}).get("target_reuse_ratio"), 0.0)
        resolved["min_fresh_generated_ratio"] = max(0.0, min(1.0, 1.0 - reuse_ratio))
    reuse = resolved.setdefault("cross_video_reuse", {})
    if "hard_max_reuse_ratio" not in reuse:
        reuse["hard_max_reuse_ratio"] = reuse.get("target_reuse_ratio", 0.0)
    if profile == "custom":
        resolved["profile"] = "custom"
    return resolved


def ratio(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def strategy_warnings(policy: dict[str, Any], *, target_minutes: float = 60.0) -> list[str]:
    reuse = ratio(policy["cross_video_reuse"].get("target_reuse_ratio"))
    extension = ratio(policy["internal_extension"].get("target_extended_track_ratio"))
    fresh_ratio = ratio(policy.get("min_fresh_generated_ratio"), 0.50)
    overlap = ratio(policy["cross_video_reuse"].get("max_overlap_with_any_same_channel_video"))
    warnings: list[str] = []
    if policy.get("profile") == "aggressive":
        warnings.append("Mayor riesgo de repetición percibida. Usar solo tras validar canal.")
    if reuse > 0.40:
        warnings.append("Reutilización por encima del 40%: riesgo medio/alto.")
    if extension > 0.50:
        warnings.append("Extensión interna por encima del 50%: riesgo medio/alto.")
    if fresh_ratio * target_minutes < min(30, target_minutes):
        warnings.append("Menos de 30 minutos frescos en vídeo de 60 min: riesgo de repetición percibida.")
    if overlap > 0.35:
        warnings.append("Solapamiento por canal superior a 35%: el catálogo puede parecer demasiado parecido.")
    return warnings


def estimate_cost(
    policy: dict[str, Any],
    *,
    target_minutes: float = 60.0,
    clip_minutes: float = 3.0,
    price_per_generation: float = 0.08,
    thumbnail_price: float = 0.134,
) -> dict[str, Any]:
    policy = resolve_asset_strategy(policy)
    base_clips = ceil(target_minutes / clip_minutes)
    base_music_cost = base_clips * price_per_generation
    base_total_cost = base_music_cost + thumbnail_price

    reuse_ratio = ratio(policy["cross_video_reuse"].get("target_reuse_ratio"))
    extension_ratio = ratio(policy["internal_extension"].get("target_extended_track_ratio"))
    preferred_factor = ratio(policy["internal_extension"].get("preferred_extension_factor"), 1.0)
    extension_intensity = min(1.0, extension_ratio / 0.35) if policy["internal_extension"].get("enabled") else 0.0
    average_extension_factor = 1 + extension_intensity * max(0.0, preferred_factor - 1)
    effective_clip_minutes = clip_minutes * average_extension_factor
    total_blocks_needed = max(1, ceil(target_minutes / effective_clip_minutes))
    expected_reused_clips = min(total_blocks_needed, int(total_blocks_needed * reuse_ratio))
    expected_new_clips = max(1, ceil(total_blocks_needed * (1 - reuse_ratio)))
    expected_extended_clips = min(
        int(ceil(total_blocks_needed * extension_ratio)),
        int(policy["internal_extension"].get("max_number_of_extended_clips") or total_blocks_needed),
    )
    optimized_music_cost = expected_new_clips * price_per_generation
    optimized_total_cost = optimized_music_cost + thumbnail_price
    savings = 0 if base_total_cost <= 0 else 1 - (optimized_total_cost / base_total_cost)

    return {
        "target_minutes": target_minutes,
        "clip_minutes": clip_minutes,
        "price_per_generation": price_per_generation,
        "thumbnail_price": thumbnail_price,
        "base_clips": base_clips,
        "base_music_cost": round(base_music_cost, 4),
        "base_total_cost": round(base_total_cost, 4),
        "average_extension_factor": round(average_extension_factor, 3),
        "effective_clip_minutes": round(effective_clip_minutes, 3),
        "total_blocks_needed": total_blocks_needed,
        "expected_new_clips": expected_new_clips,
        "expected_reused_clips": expected_reused_clips,
        "expected_extended_clips": expected_extended_clips,
        "optimized_music_cost": round(optimized_music_cost, 4),
        "optimized_total_cost": round(optimized_total_cost, 4),
        "expected_savings_ratio": round(savings, 4),
        "expected_savings_percent": round(savings * 100, 2),
        "cost_per_100_videos": round(optimized_total_cost * 100, 2),
        "cost_per_500_videos": round(optimized_total_cost * 500, 2),
        "cost_per_1000_videos": round(optimized_total_cost * 1000, 2),
        "warnings": strategy_warnings(policy, target_minutes=target_minutes),
    }
