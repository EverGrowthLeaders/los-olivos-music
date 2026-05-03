from yt_music_factory.asset_strategy import strategy_preset
from yt_music_factory.timeline.planner import plan_timeline


def _track(idx: int, *, reused: bool = False, slot_entries=None) -> dict:
    return {
        "id": f"trk_{idx:02d}",
        "source_file": f"/tmp/trk_{idx:02d}.wav",
        "duration_seconds": 180,
        "substyle": "focus_lofi",
        "energy": "medium",
        "bpm": None,
        "key": None,
        "reusable": True,
        "loop_safe": True,
        "last_used_slot_indexes": [99] if reused else [],
        "last_used_slot_entries": slot_entries or [],
    }


def _policy_without_extension():
    policy = strategy_preset("standard")
    policy["internal_extension"]["enabled"] = False
    policy["min_fresh_generated_ratio"] = 0.20
    policy["intro_outro"]["first_minutes_must_be_new"] = 6
    policy["intro_outro"]["last_minutes_must_be_new"] = 3
    return policy


def test_planner_respects_intro_outro_and_no_duplicates():
    policy = _policy_without_extension()
    manifest = plan_timeline(
        job_id="job-a",
        channel_id="channel",
        theme="Focus",
        target_seconds=1800,
        new_tracks=[_track(i) for i in range(20)],
        reusable_tracks=[_track(i, reused=True) for i in range(100, 110)],
        policy=policy,
        history=[],
    )
    items = manifest["timeline"]
    ids = [item["track_id"] for item in items]

    assert manifest["gate_passed"]
    assert all(item["is_new"] for item in items if item["start_seconds"] < 360)
    assert items[-1]["is_new"]
    assert len(ids) == len(set(ids))


def test_planner_respects_reuse_ratio_approximately():
    policy = _policy_without_extension()
    manifest = plan_timeline(
        job_id="job-b",
        channel_id="channel",
        theme="Focus",
        target_seconds=1800,
        new_tracks=[_track(i) for i in range(20)],
        reusable_tracks=[_track(i, reused=True) for i in range(100, 120)],
        policy=policy,
        history=[],
    )

    assert 0.15 <= manifest["reuse_ratio"] <= 0.40


def test_planner_extends_without_consecutive_extended_clips():
    policy = strategy_preset("standard")
    policy["cross_video_reuse"]["enabled"] = False
    policy["min_fresh_generated_ratio"] = 0
    policy["intro_outro"]["first_minutes_must_be_new"] = 0
    policy["intro_outro"]["last_minutes_must_be_new"] = 0
    manifest = plan_timeline(
        job_id="job-c",
        channel_id="channel",
        theme="Focus",
        target_seconds=1800,
        new_tracks=[_track(i) for i in range(20)],
        reusable_tracks=[],
        policy=policy,
        history=[],
    )
    extended = [item["is_extended"] for item in manifest["timeline"]]

    assert any(extended)
    assert not any(a and b for a, b in zip(extended, extended[1:]))
    assert sum(extended) <= policy["internal_extension"]["max_number_of_extended_clips"]


def test_planner_rejects_repeated_bigrams_and_trigrams():
    policy = _policy_without_extension()
    policy["min_fresh_generated_ratio"] = 0
    policy["intro_outro"]["first_minutes_must_be_new"] = 0
    policy["intro_outro"]["last_minutes_must_be_new"] = 0
    policy["intro_outro"]["second_track_must_be_new"] = False
    history = [{"timeline": [{"track_id": "trk_00"}, {"track_id": "trk_01"}, {"track_id": "trk_02"}]}]
    manifest = plan_timeline(
        job_id="job-d",
        channel_id="channel",
        theme="Focus",
        target_seconds=360,
        new_tracks=[_track(0), _track(1), _track(2), _track(3)],
        reusable_tracks=[],
        policy=policy,
        history=history,
    )
    ids = [item["track_id"] for item in manifest["timeline"]]

    assert ids[:2] != ["trk_00", "trk_01"]
    assert any(candidate["reason"] == "historical_bigram" for candidate in manifest["rejected_candidates"])
