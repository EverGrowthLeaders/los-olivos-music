from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
import random
import time
from typing import Any, Sequence

from ..asset_strategy import ratio
from ..repetition.validators import (
    calculate_audio_overlap,
    calculate_ordered_bigrams,
    calculate_ordered_trigrams,
    validate_publish_gate,
)


@dataclass(slots=True)
class TimelineItem:
    track_id: str
    source_file: str
    start_seconds: int
    planned_duration_seconds: int
    original_duration_seconds: int
    is_new: bool
    is_reused: bool
    is_extended: bool
    extension_factor: float
    crossfade_seconds: int
    slot_index: int
    substyle: str
    energy: str | None = None
    bpm: int | None = None
    key: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def estimate_new_track_count(
    *,
    target_seconds: int,
    source_track_seconds: int,
    available_reusable_count: int,
    policy: dict[str, Any],
) -> int:
    extension = policy.get("internal_extension", {})
    reuse = policy.get("cross_video_reuse", {})
    preferred_factor = ratio(extension.get("preferred_extension_factor"), 1.0) if extension.get("enabled") else 1.0
    extended_ratio = ratio(extension.get("target_extended_track_ratio")) if extension.get("enabled") else 0.0
    average_factor = 1 + extended_ratio * max(0.0, preferred_factor - 1)
    block_seconds = max(1, int(source_track_seconds * average_factor))
    total_blocks = max(1, ceil(target_seconds / block_seconds))
    target_reused = min(
        available_reusable_count,
        int(total_blocks * ratio(reuse.get("target_reuse_ratio"))) if reuse.get("enabled") else 0,
    )
    min_fresh_seconds = _min_fresh_seconds(policy, target_seconds)
    min_fresh_tracks = ceil(min_fresh_seconds / max(1, source_track_seconds))
    return max(1, min_fresh_tracks, total_blocks - target_reused)


def plan_timeline(
    *,
    job_id: str,
    channel_id: str,
    theme: str,
    target_seconds: int,
    new_tracks: Sequence[dict[str, Any]],
    reusable_tracks: Sequence[dict[str, Any]],
    policy: dict[str, Any],
    history: Sequence[dict[str, Any]],
    other_channel_history: Sequence[dict[str, Any]] | None = None,
    shuffle_seed: str | None = None,
) -> dict[str, Any]:
    source_seconds = int(new_tracks[0]["duration_seconds"] if new_tracks else 180)
    slot_seconds = int(policy.get("position", {}).get("slot_seconds") or source_seconds or 180)
    intro = policy.get("intro_outro", {})
    extension_policy = policy.get("internal_extension", {})
    reuse_policy = policy.get("cross_video_reuse", {})

    first_new_seconds = int(float(intro.get("first_minutes_must_be_new") or 0) * 60)
    last_new_seconds = int(float(intro.get("last_minutes_must_be_new") or 0) * 60)
    forbid_extension_first = int(float(intro.get("forbid_extension_in_first_minutes") or 0) * 60)
    forbid_extension_last = int(float(intro.get("forbid_extension_in_last_minutes") or 0) * 60)
    max_extended = int(extension_policy.get("max_number_of_extended_clips") or 0)
    target_extended_ratio = ratio(extension_policy.get("target_extended_track_ratio")) if extension_policy.get("enabled") else 0.0
    max_looped_ratio = ratio(extension_policy.get("max_looped_minutes_ratio"))
    target_reuse_ratio = ratio(reuse_policy.get("target_reuse_ratio")) if reuse_policy.get("enabled") else 0.0
    hard_max_reuse_ratio = ratio(reuse_policy.get("hard_max_reuse_ratio"), 1.0)
    min_fresh_seconds = _min_fresh_seconds(policy, target_seconds)

    historical_sequences = [[str(item.get("track_id")) for item in video.get("timeline", [])] for video in history]
    historical_bigrams = set().union(*(calculate_ordered_bigrams(sequence) for sequence in historical_sequences)) if historical_sequences else set()
    historical_trigrams = set().union(*(calculate_ordered_trigrams(sequence) for sequence in historical_sequences)) if historical_sequences else set()
    used_ids: set[str] = set()
    rejected: list[dict[str, Any]] = []
    timeline: list[TimelineItem] = []
    elapsed = 0
    extended_count = 0
    reused_count = 0
    reused_seconds = 0
    fresh_seconds = 0
    looped_seconds = 0
    new_queue = list(new_tracks)
    reusable_queue = list(reusable_tracks)
    random.Random(shuffle_seed or job_id).shuffle(reusable_queue)

    while elapsed < target_seconds:
        remaining = target_seconds - elapsed
        slot_index = elapsed // max(1, slot_seconds)
        force_new = (
            elapsed < first_new_seconds
            or remaining <= last_new_seconds
            or (intro.get("last_track_must_be_new") and remaining <= source_seconds)
            or (intro.get("first_track_must_be_new") and not timeline)
            or (intro.get("second_track_must_be_new") and len(timeline) == 1)
            or (fresh_seconds < min_fresh_seconds and bool(new_queue))
        )
        current_reuse_ratio = reused_seconds / max(1, elapsed)
        wants_reuse = (
            not force_new
            and reusable_queue
            and current_reuse_ratio < target_reuse_ratio
            and (reused_seconds + source_seconds) / target_seconds <= hard_max_reuse_ratio
        )
        track = _pop_candidate(
            reusable_queue if wants_reuse else new_queue,
            used_ids,
            timeline,
            historical_bigrams,
            historical_trigrams,
            policy,
            slot_index,
            rejected,
        )
        is_reused = wants_reuse and track is not None
        if track is None:
            track = _pop_candidate(new_queue, used_ids, timeline, historical_bigrams, historical_trigrams, policy, slot_index, rejected)
            is_reused = False
        if track is None:
            # As a last resort, reuse a new track already present in this render is forbidden by policy,
            # so fail early instead of silently creating a near-duplicate timeline.
            raise RuntimeError("Not enough eligible fresh/reusable tracks to build a non-repeating timeline")

        original_duration = max(1, int(round(float(track.get("duration_seconds") or source_seconds))))
        can_extend = _can_extend(
            track,
            elapsed,
            remaining,
            policy,
            extended_count=extended_count,
            previous_extended=bool(timeline and timeline[-1].is_extended),
            looped_seconds=looped_seconds,
            target_seconds=target_seconds,
            forbid_extension_first=forbid_extension_first,
            forbid_extension_last=forbid_extension_last,
        )
        target_extended_count = ceil(max(1, len(new_tracks) + len(reusable_tracks)) * target_extended_ratio)
        is_extended = can_extend and extended_count < target_extended_count
        if is_extended:
            extension_factor = min(
                float(extension_policy.get("preferred_extension_factor") or 1.85),
                float(extension_policy.get("max_extension_factor") or 2.0),
            )
            planned_duration = min(
                int(original_duration * extension_factor),
                int(extension_policy.get("max_extended_duration_seconds") or original_duration * 2),
                remaining,
            )
            crossfade = int(extension_policy.get("preferred_crossfade_seconds") or 18)
            extended_count += 1
            looped_seconds += max(0, planned_duration - original_duration)
        else:
            extension_factor = 1.0
            planned_duration = min(original_duration, remaining)
            crossfade = 0

        item = TimelineItem(
            track_id=str(track["id"]),
            source_file=str(track["source_file"]),
            start_seconds=elapsed,
            planned_duration_seconds=planned_duration,
            original_duration_seconds=original_duration,
            is_new=not is_reused,
            is_reused=is_reused,
            is_extended=is_extended,
            extension_factor=round(extension_factor, 3),
            crossfade_seconds=crossfade,
            slot_index=int(slot_index),
            substyle=str(track.get("substyle") or theme),
            energy=track.get("energy"),
            bpm=track.get("bpm"),
            key=track.get("key"),
        )
        timeline.append(item)
        used_ids.add(item.track_id)
        if is_reused:
            reused_count += 1
            reused_seconds += planned_duration
        else:
            fresh_seconds += planned_duration
        elapsed += planned_duration

        if fresh_seconds < min_fresh_seconds and not new_queue and elapsed < target_seconds:
            rejected.append({"reason": "min_fresh_generated_ratio_at_risk", "fresh_seconds": fresh_seconds})

    manifest = {
        "job_id": job_id,
        "channel_id": channel_id,
        "theme": theme,
        "created_at": time.time(),
        "policy_profile": policy.get("profile", "standard"),
        "total_duration_seconds": target_seconds,
        "fresh_generated_minutes": round(fresh_seconds / 60, 3),
        "reused_minutes": round(reused_seconds / 60, 3),
        "internally_extended_minutes": round(looped_seconds / 60, 3),
        "reuse_ratio": round(reused_seconds / max(1, target_seconds), 4),
        "extension_ratio": round(looped_seconds / max(1, target_seconds), 4),
        "timeline": [item.to_json() for item in timeline],
        "rejected_candidates": rejected,
        "risk_scores": {},
        "gate_passed": False,
    }
    gate = validate_publish_gate(manifest, policy, history)
    manifest["risk_scores"] = gate["scores"]
    manifest["gate_failures"] = gate["failures"]
    manifest["gate_passed"] = gate["passed"]
    _apply_overlap_gates(manifest, policy, history, other_channel_history or [])
    if manifest["reuse_ratio"] > hard_max_reuse_ratio:
        manifest["gate_passed"] = False
        manifest.setdefault("gate_failures", []).append(
            {"score": "reuse_ratio", "value": manifest["reuse_ratio"], "limit": hard_max_reuse_ratio}
        )
    if max_looped_ratio and manifest["extension_ratio"] > max_looped_ratio:
        manifest["gate_passed"] = False
        manifest.setdefault("gate_failures", []).append(
            {"score": "extension_ratio", "value": manifest["extension_ratio"], "limit": max_looped_ratio}
        )
    return manifest


def _pop_candidate(
    queue: list[dict[str, Any]],
    used_ids: set[str],
    timeline: list[TimelineItem],
    historical_bigrams: set[tuple[str, str]],
    historical_trigrams: set[tuple[str, str, str]],
    policy: dict[str, Any],
    slot_index: int,
    rejected: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for idx, track in enumerate(list(queue)):
        track_id = str(track.get("id"))
        if track_id in used_ids:
            rejected.append({"track_id": track_id, "reason": "duplicate_within_video"})
            continue
        if policy.get("sequence", {}).get("forbid_repeated_ordered_bigrams") and timeline:
            if (timeline[-1].track_id, track_id) in historical_bigrams:
                rejected.append({"track_id": track_id, "reason": "historical_bigram"})
                continue
        if policy.get("sequence", {}).get("forbid_repeated_ordered_trigrams") and len(timeline) >= 2:
            if (timeline[-2].track_id, timeline[-1].track_id, track_id) in historical_trigrams:
                rejected.append({"track_id": track_id, "reason": "historical_trigram"})
                continue
        if policy.get("position", {}).get("forbid_same_slot_reuse") and slot_index in set(track.get("last_used_slot_indexes") or []):
            rejected.append({"track_id": track_id, "reason": "same_slot_reuse"})
            continue
        neighbor_days = int(policy.get("position", {}).get("forbid_neighbor_slot_reuse_within_days") or 0)
        if neighbor_days:
            now = time.time()
            cooldown_seconds = neighbor_days * 86400
            for entry in track.get("last_used_slot_entries") or []:
                used_slot = int(entry.get("slot_index") or 0)
                used_at = float(entry.get("used_at") or 0)
                if abs(used_slot - slot_index) <= 1 and used_at and now - used_at <= cooldown_seconds:
                    rejected.append({"track_id": track_id, "reason": "neighbor_slot_cooldown"})
                    break
            else:
                queue.pop(idx)
                return track
            continue
        queue.pop(idx)
        return track
    return None


def _min_fresh_seconds(policy: dict[str, Any], target_seconds: int) -> int:
    if "min_fresh_generated_ratio" in policy:
        return min(target_seconds, int(target_seconds * ratio(policy.get("min_fresh_generated_ratio"), 0.0)))
    return min(target_seconds, int(ratio(policy.get("min_fresh_generated_minutes"), 0.0) * 60))


def _apply_overlap_gates(
    manifest: dict[str, Any],
    policy: dict[str, Any],
    history: Sequence[dict[str, Any]],
    other_channel_history: Sequence[dict[str, Any]],
) -> None:
    reuse = policy.get("cross_video_reuse", {})
    if history:
        previous_score = calculate_audio_overlap(manifest, history[0])
        limit = ratio(reuse.get("max_overlap_with_previous_video"), 1.0)
        manifest["risk_scores"]["previous_video_overlap_score"] = round(previous_score, 4)
        if previous_score > limit:
            manifest["gate_passed"] = False
            manifest.setdefault("gate_failures", []).append(
                {"score": "previous_video_overlap_score", "value": round(previous_score, 4), "limit": limit}
            )
        same_channel_score = max(calculate_audio_overlap(manifest, video) for video in history)
        same_limit = ratio(reuse.get("max_overlap_with_any_same_channel_video"), 1.0)
        manifest["risk_scores"]["same_channel_overlap_score"] = round(same_channel_score, 4)
        if same_channel_score > same_limit:
            manifest["gate_passed"] = False
            manifest.setdefault("gate_failures", []).append(
                {"score": "same_channel_overlap_score", "value": round(same_channel_score, 4), "limit": same_limit}
            )
    if other_channel_history:
        other_score = max(calculate_audio_overlap(manifest, video) for video in other_channel_history)
        other_limit = ratio(reuse.get("max_overlap_with_other_owned_channels"), 1.0)
        manifest["risk_scores"]["other_owned_channels_overlap_score"] = round(other_score, 4)
        if other_score > other_limit:
            manifest["gate_passed"] = False
            manifest.setdefault("gate_failures", []).append(
                {"score": "other_owned_channels_overlap_score", "value": round(other_score, 4), "limit": other_limit}
            )


def _can_extend(
    track: dict[str, Any],
    elapsed: int,
    remaining: int,
    policy: dict[str, Any],
    *,
    extended_count: int,
    previous_extended: bool,
    looped_seconds: int,
    target_seconds: int,
    forbid_extension_first: int,
    forbid_extension_last: int,
) -> bool:
    extension = policy.get("internal_extension", {})
    if not extension.get("enabled"):
        return False
    if not track.get("loop_safe", True):
        return False
    if elapsed < forbid_extension_first or remaining <= forbid_extension_last:
        return False
    if extended_count >= int(extension.get("max_number_of_extended_clips") or 0):
        return False
    if previous_extended and not extension.get("allow_consecutive_extended_clips", False):
        return False
    original = int(float(track.get("duration_seconds") or 180))
    projected_looped = looped_seconds + max(0, int(original * float(extension.get("preferred_extension_factor") or 1.85)) - original)
    if projected_looped / max(1, target_seconds) > ratio(extension.get("max_looped_minutes_ratio")):
        return False
    return True


def timeline_source_paths(manifest: dict[str, Any]) -> list[Path]:
    return [Path(item["source_file"]) for item in manifest.get("timeline", [])]
