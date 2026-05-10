import json

from yt_music_factory.analytics import (
    AnalyticsStore,
    apply_learning_profile_to_spec,
    calculate_performance_score,
    diagnose_performance,
    recommend_actions,
)
from yt_music_factory.config import JobConfig, JobSpec
from yt_music_factory.youtube import (
    YOUTUBE_ANALYTICS_SCOPE,
    YOUTUBE_READONLY_SCOPE,
    YOUTUBE_UPLOAD_SCOPE,
    youtube_oauth_scopes,
)


def test_performance_score_identifies_winner_pattern():
    metrics = {
        "views": 1000,
        "videoThumbnailImpressions": 1000,
        "videoThumbnailImpressionsClickRate": 0.08,
        "estimatedMinutesWatched": 1500,
        "averageViewPercentage": 42,
        "likes": 90,
        "comments": 10,
        "shares": 12,
        "subscribersGained": 25,
        "subscribersLost": 0,
        "estimatedRevenue": 12,
    }

    score = calculate_performance_score(metrics, creative={"job": {"target_seconds": 3600}})
    diagnosis = diagnose_performance(metrics, score)
    recommendations = recommend_actions(metrics, score, diagnosis, creative={"category_key": "deep_trading"})

    assert score["performance_score"] >= 70
    assert "winner" in diagnosis["labels"]
    assert any(rec["target"] == "music" for rec in recommendations)
    assert any(rec["target"] == "thumbnail" for rec in recommendations)


def test_analytics_store_applies_recommendation_to_learning_profile(tmp_path):
    manifest_path = tmp_path / "creative_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "category_key": "deep_trading",
                "job": {"target_seconds": 3600},
                "category": {"label": "Deep Trading", "music_prompt": "steady electronic focus"},
                "metadata": {"title": "Deep Trading Mix", "tags": ["focus"]},
                "timeline_summary": {"new_count": 12, "extended_count": 4},
            }
        ),
        encoding="utf-8",
    )
    store = AnalyticsStore(tmp_path / "analytics.sqlite")
    run = {
        "youtube_video_id": "abc123",
        "slug": "deep-trading",
        "creative_manifest_path": str(manifest_path),
        "thumbnail": str(tmp_path / "thumb.jpg"),
    }
    metrics = {
        "views": 1000,
        "videoThumbnailImpressions": 1000,
        "videoThumbnailImpressionsClickRate": 0.08,
        "estimatedMinutesWatched": 1500,
        "averageViewPercentage": 42,
        "likes": 90,
        "comments": 10,
        "shares": 12,
        "subscribersGained": 25,
    }

    store.upsert_video_performance(tenant_id="default", run=run, metrics=metrics)
    summary = store.summary(tenant_id="default", runs=[run])
    rec = next(item for item in summary["pending_recommendations"] if item["target"] == "music")

    store.apply_recommendation(tenant_id="default", recommendation_id=rec["id"], status="applied")
    spec = JobSpec(job=JobConfig(slug="next"), category_key="deep_trading")
    profile = apply_learning_profile_to_spec(
        spec=spec,
        category={"music_prompt": "steady electronic focus"},
        db_path=tmp_path / "analytics.sqlite",
        channel_id="default",
    )

    assert profile["enabled"] is True
    assert profile["version"] == 1
    assert "Performance learning guidance for music" in spec.music.prompt


def test_youtube_oauth_scopes_include_upload_read_and_analytics():
    scopes = youtube_oauth_scopes(include_monetary=False)

    assert YOUTUBE_UPLOAD_SCOPE in scopes
    assert YOUTUBE_READONLY_SCOPE in scopes
    assert YOUTUBE_ANALYTICS_SCOPE in scopes
