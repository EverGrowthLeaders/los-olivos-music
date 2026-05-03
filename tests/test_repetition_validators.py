from yt_music_factory.asset_strategy import strategy_preset
from yt_music_factory.repetition.validators import (
    calculate_audio_overlap,
    calculate_ordered_bigrams,
    calculate_ordered_trigrams,
    calculate_position_overlap,
    has_repeated_bigram,
    has_repeated_trigram,
    longest_common_subsequence_ratio,
    validate_publish_gate,
)


def test_bigram_and_trigram_detection():
    sequence = ["a", "b", "c", "d"]

    assert calculate_ordered_bigrams(sequence) == {("a", "b"), ("b", "c"), ("c", "d")}
    assert calculate_ordered_trigrams(sequence) == {("a", "b", "c"), ("b", "c", "d")}
    assert has_repeated_bigram(["x", "b", "c"], [["a", "b", "c"]])
    assert has_repeated_trigram(["a", "b", "c"], [["z", "a", "b", "c"]])


def test_lcs_and_overlap_scores():
    assert longest_common_subsequence_ratio(["a", "b", "c"], ["a", "x", "c"]) == 2 / 3
    assert calculate_audio_overlap(["a", "b", "c"], ["b", "c", "z"]) == 2 / 3


def test_position_overlap_counts_same_and_neighbor_slots():
    candidate = [{"track_id": "a", "slot_index": 3}, {"track_id": "b", "slot_index": 8}]
    historical = [{"track_id": "a", "slot_index": 4}, {"track_id": "b", "slot_index": 1}]

    assert calculate_position_overlap(candidate, historical) == 0.5
    assert calculate_position_overlap(candidate, historical, allow_neighbor=False) == 0.0


def test_publish_gate_rejects_high_repetition():
    policy = strategy_preset("standard")
    candidate = {"timeline": [{"track_id": "a", "slot_index": 0}, {"track_id": "b", "slot_index": 1}]}
    history = [{"timeline": [{"track_id": "a", "slot_index": 0}, {"track_id": "b", "slot_index": 1}]}]

    gate = validate_publish_gate(candidate, policy, history)

    assert not gate["passed"]
    assert {failure["score"] for failure in gate["failures"]} >= {
        "audio_overlap_score",
        "sequence_similarity_score",
        "position_similarity_score",
    }
