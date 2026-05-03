from __future__ import annotations

from typing import Any, Iterable, Sequence


def calculate_ordered_bigrams(track_ids: Sequence[str]) -> set[tuple[str, str]]:
    return set(zip(track_ids, track_ids[1:]))


def calculate_ordered_trigrams(track_ids: Sequence[str]) -> set[tuple[str, str, str]]:
    return set(zip(track_ids, track_ids[1:], track_ids[2:]))


def has_repeated_bigram(candidate_sequence: Sequence[str], historical_sequences: Iterable[Sequence[str]]) -> bool:
    candidate = calculate_ordered_bigrams(candidate_sequence)
    return any(candidate & calculate_ordered_bigrams(sequence) for sequence in historical_sequences)


def has_repeated_trigram(candidate_sequence: Sequence[str], historical_sequences: Iterable[Sequence[str]]) -> bool:
    candidate = calculate_ordered_trigrams(candidate_sequence)
    return any(candidate & calculate_ordered_trigrams(sequence) for sequence in historical_sequences)


def longest_common_subsequence_ratio(seq_a: Sequence[str], seq_b: Sequence[str]) -> float:
    if not seq_a or not seq_b:
        return 0.0
    previous = [0] * (len(seq_b) + 1)
    for item_a in seq_a:
        current = [0]
        for idx_b, item_b in enumerate(seq_b, start=1):
            if item_a == item_b:
                current.append(previous[idx_b - 1] + 1)
            else:
                current.append(max(previous[idx_b], current[-1]))
        previous = current
    return previous[-1] / max(len(seq_a), len(seq_b), 1)


def calculate_audio_overlap(video_a: Sequence[str] | dict[str, Any], video_b: Sequence[str] | dict[str, Any]) -> float:
    ids_a = set(_track_ids(video_a))
    ids_b = set(_track_ids(video_b))
    if not ids_a:
        return 0.0
    return len(ids_a & ids_b) / len(ids_a)


def calculate_position_overlap(
    candidate_timeline: Sequence[dict[str, Any]],
    historical_timeline: Sequence[dict[str, Any]],
    *,
    allow_neighbor: bool = True,
) -> float:
    if not candidate_timeline:
        return 0.0
    historical_slots: dict[str, set[int]] = {}
    for item in historical_timeline:
        historical_slots.setdefault(str(item.get("track_id")), set()).add(int(item.get("slot_index") or 0))

    overlap = 0
    for item in candidate_timeline:
        track_id = str(item.get("track_id"))
        slot = int(item.get("slot_index") or 0)
        slots = historical_slots.get(track_id, set())
        if slot in slots or (allow_neighbor and any(abs(slot - historical_slot) <= 1 for historical_slot in slots)):
            overlap += 1
    return overlap / len(candidate_timeline)


def calculate_total_repetition_risk(scores: dict[str, float]) -> float:
    return round(
        scores.get("audio_overlap_score", 0.0) * 0.35
        + scores.get("sequence_similarity_score", 0.0) * 0.25
        + scores.get("position_similarity_score", 0.0) * 0.20
        + scores.get("metadata_similarity_score", 0.0) * 0.10
        + scores.get("visual_similarity_score", 0.0) * 0.10,
        4,
    )


def calculate_candidate_scores(candidate_manifest: dict[str, Any], history: Sequence[dict[str, Any]]) -> dict[str, float]:
    candidate_timeline = candidate_manifest.get("timeline") or []
    candidate_ids = _track_ids(candidate_manifest)
    historical_sequences = [_track_ids(video) for video in history]

    audio_overlap = max((calculate_audio_overlap(candidate_ids, sequence) for sequence in historical_sequences), default=0.0)
    sequence_similarity = max(
        (longest_common_subsequence_ratio(candidate_ids, sequence) for sequence in historical_sequences),
        default=0.0,
    )
    position_similarity = max(
        (
            calculate_position_overlap(candidate_timeline, video.get("timeline") or [])
            for video in history
        ),
        default=0.0,
    )
    scores = {
        "audio_overlap_score": round(audio_overlap, 4),
        "sequence_similarity_score": round(sequence_similarity, 4),
        "position_similarity_score": round(position_similarity, 4),
        "metadata_similarity_score": 0.0,
        "visual_similarity_score": 0.0,
    }
    scores["total_repetition_risk_score"] = calculate_total_repetition_risk(scores)
    return scores


def validate_publish_gate(
    candidate_manifest: dict[str, Any],
    policy: dict[str, Any],
    history: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    scores = calculate_candidate_scores(candidate_manifest, history)
    gate = policy.get("publish_gate", {})
    failures = []
    checks = {
        "audio_overlap_score": gate.get("max_audio_overlap_score", 1.0),
        "sequence_similarity_score": gate.get("max_sequence_similarity_score", 1.0),
        "position_similarity_score": gate.get("max_position_similarity_score", 1.0),
        "metadata_similarity_score": gate.get("max_metadata_similarity_score", 1.0),
        "visual_similarity_score": gate.get("max_visual_similarity_score", 1.0),
        "total_repetition_risk_score": gate.get("max_total_repetition_risk_score", 1.0),
    }
    for key, limit in checks.items():
        if scores.get(key, 0.0) > float(limit):
            failures.append({"score": key, "value": scores[key], "limit": float(limit)})
    return {"passed": not failures, "scores": scores, "failures": failures}


def _track_ids(video: Sequence[str] | dict[str, Any]) -> list[str]:
    if isinstance(video, dict):
        return [str(item.get("track_id")) for item in video.get("timeline", []) if item.get("track_id")]
    return [str(item) for item in video]

