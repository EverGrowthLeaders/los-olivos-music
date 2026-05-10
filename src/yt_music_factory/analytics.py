from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import JobSpec
from .seo import VideoMetadata
from .utils import ensure_dir, write_json

YOUTUBE_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_MONETARY_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"

CORE_ANALYTICS_METRICS = [
    "views",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "likes",
    "comments",
    "shares",
    "subscribersGained",
    "subscribersLost",
]
REACH_METRICS = ["videoThumbnailImpressions", "videoThumbnailImpressionsClickRate"]
RETENTION_METRICS = ["audienceWatchRatio", "relativeRetentionPerformance"]


class AnalyticsStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        ensure_dir(db_path.parent)
        self._init_db()

    def upsert_video_performance(
        self,
        *,
        tenant_id: str,
        run: dict[str, Any],
        metrics: dict[str, Any],
        retention_points: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        video_id = str(run.get("youtube_video_id") or metrics.get("video_id") or "")
        if not video_id:
            raise ValueError("youtube_video_id is required")
        creative = load_creative_manifest(run)
        category_key = str(creative.get("category_key") or run.get("category_key") or "")
        score = calculate_performance_score(metrics, creative=creative)
        diagnosis = diagnose_performance(metrics, score, creative=creative)
        recommendations = recommend_actions(metrics, score, diagnosis, creative=creative)
        now = time.time()
        payload = {
            "tenant_id": tenant_id,
            "video_id": video_id,
            "slug": run.get("slug"),
            "category_key": category_key,
            "collected_at": now,
            "metrics": metrics,
            "score": score,
            "diagnosis": diagnosis,
            "recommendations": recommendations,
            "creative": summarize_creative_manifest(creative),
            "thumbnail": run.get("thumbnail"),
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO latest_video_metrics (
                  tenant_id, video_id, slug, category_key, collected_at,
                  metrics_json, score_json, diagnosis_json, creative_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, video_id) DO UPDATE SET
                  slug=excluded.slug,
                  category_key=excluded.category_key,
                  collected_at=excluded.collected_at,
                  metrics_json=excluded.metrics_json,
                  score_json=excluded.score_json,
                  diagnosis_json=excluded.diagnosis_json,
                  creative_json=excluded.creative_json
                """,
                (
                    tenant_id,
                    video_id,
                    run.get("slug"),
                    category_key,
                    now,
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(score, ensure_ascii=False),
                    json.dumps(diagnosis, ensure_ascii=False),
                    json.dumps(payload["creative"], ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO metric_snapshots (
                  tenant_id, video_id, slug, category_key, collected_at,
                  metrics_json, score_json, diagnosis_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    video_id,
                    run.get("slug"),
                    category_key,
                    now,
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(score, ensure_ascii=False),
                    json.dumps(diagnosis, ensure_ascii=False),
                ),
            )
            if retention_points is not None:
                conn.execute(
                    "DELETE FROM retention_points WHERE tenant_id=? AND video_id=?",
                    (tenant_id, video_id),
                )
                conn.executemany(
                    """
                    INSERT INTO retention_points (
                      tenant_id, video_id, elapsed_ratio, audience_watch_ratio,
                      relative_retention_performance, collected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            tenant_id,
                            video_id,
                            float(point.get("elapsedVideoTimeRatio") or 0),
                            _float_or_none(point.get("audienceWatchRatio")),
                            _float_or_none(point.get("relativeRetentionPerformance")),
                            now,
                        )
                        for point in retention_points
                    ],
                )
            self._upsert_recommendations(conn, tenant_id, video_id, run, category_key, recommendations)
        return payload

    def summary(self, *, tenant_id: str, runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        run_map = {str(run.get("youtube_video_id")): run for run in runs or [] if run.get("youtube_video_id")}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT video_id, slug, category_key, collected_at, metrics_json,
                       score_json, diagnosis_json, creative_json
                FROM latest_video_metrics
                WHERE tenant_id=?
                ORDER BY json_extract(score_json, '$.performance_score') DESC
                """,
                (tenant_id,),
            ).fetchall()
            rec_rows = conn.execute(
                """
                SELECT id, video_id, slug, category_key, created_at, status, recommendation_json
                FROM recommendations
                WHERE tenant_id=? AND status='pending'
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (tenant_id,),
            ).fetchall()
            profile_rows = conn.execute(
                """
                SELECT category_key, updated_at, profile_json
                FROM learning_profiles
                WHERE tenant_id=?
                ORDER BY updated_at DESC
                """,
                (tenant_id,),
            ).fetchall()
        videos = []
        for row in rows:
            video_id, slug, category_key, collected_at, metrics_json, score_json, diagnosis_json, creative_json = row
            run = run_map.get(video_id, {})
            videos.append(
                {
                    "video_id": video_id,
                    "slug": slug,
                    "category_key": category_key,
                    "collected_at": collected_at,
                    "metrics": json.loads(metrics_json),
                    "score": json.loads(score_json),
                    "diagnosis": json.loads(diagnosis_json),
                    "creative": json.loads(creative_json),
                    "thumbnail": run.get("thumbnail"),
                    "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
                }
            )
        return {
            "updated_at": max([video["collected_at"] for video in videos], default=None),
            "video_count": len(videos),
            "top_videos": videos[:20],
            "category_breakdown": _category_breakdown(videos),
            "pending_recommendations": [
                {
                    "id": row[0],
                    "video_id": row[1],
                    "slug": row[2],
                    "category_key": row[3],
                    "created_at": row[4],
                    "status": row[5],
                    **json.loads(row[6]),
                }
                for row in rec_rows
            ],
            "learning_profiles": [
                {"category_key": row[0], "updated_at": row[1], **json.loads(row[2])}
                for row in profile_rows
            ],
        }

    def video_detail(self, *, tenant_id: str, video_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT video_id, slug, category_key, collected_at, metrics_json,
                       score_json, diagnosis_json, creative_json
                FROM latest_video_metrics
                WHERE tenant_id=? AND video_id=?
                """,
                (tenant_id, video_id),
            ).fetchone()
            retention_rows = conn.execute(
                """
                SELECT elapsed_ratio, audience_watch_ratio, relative_retention_performance
                FROM retention_points
                WHERE tenant_id=? AND video_id=?
                ORDER BY elapsed_ratio
                """,
                (tenant_id, video_id),
            ).fetchall()
        if not row:
            raise KeyError(video_id)
        return {
            "video_id": row[0],
            "slug": row[1],
            "category_key": row[2],
            "collected_at": row[3],
            "metrics": json.loads(row[4]),
            "score": json.loads(row[5]),
            "diagnosis": json.loads(row[6]),
            "creative": json.loads(row[7]),
            "retention": [
                {
                    "elapsedVideoTimeRatio": item[0],
                    "audienceWatchRatio": item[1],
                    "relativeRetentionPerformance": item[2],
                }
                for item in retention_rows
            ],
        }

    def apply_recommendation(self, *, tenant_id: str, recommendation_id: str, status: str = "applied") -> dict[str, Any]:
        if status not in {"applied", "ignored"}:
            raise ValueError("status must be applied or ignored")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT category_key, recommendation_json
                FROM recommendations
                WHERE tenant_id=? AND id=?
                """,
                (tenant_id, recommendation_id),
            ).fetchone()
            if not row:
                raise KeyError(recommendation_id)
            category_key, recommendation_json = row
            recommendation = json.loads(recommendation_json)
            conn.execute(
                """
                UPDATE recommendations
                SET status=?, updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (status, now, tenant_id, recommendation_id),
            )
            if status == "applied":
                profile = self._load_learning_profile_conn(conn, tenant_id, category_key)
                guidance = profile.setdefault("approved_guidance", {})
                target = recommendation.get("target") or "general"
                entries = list(guidance.get(target) or [])
                text = recommendation.get("guidance") or recommendation.get("title") or ""
                if text and text not in entries:
                    entries.append(text)
                guidance[target] = entries[-10:]
                profile["enabled"] = True
                profile["version"] = int(profile.get("version") or 0) + 1
                profile["updated_at"] = now
                profile["last_applied_recommendation_id"] = recommendation_id
                conn.execute(
                    """
                    INSERT INTO learning_profiles (tenant_id, category_key, updated_at, profile_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(tenant_id, category_key) DO UPDATE SET
                      updated_at=excluded.updated_at,
                      profile_json=excluded.profile_json
                    """,
                    (tenant_id, category_key, now, json.dumps(profile, ensure_ascii=False)),
                )
        return {"id": recommendation_id, "status": status}

    def load_learning_profile(self, tenant_id: str, category_key: str) -> dict[str, Any]:
        with self._connect() as conn:
            return self._load_learning_profile_conn(conn, tenant_id, category_key)

    def _load_learning_profile_conn(self, conn: sqlite3.Connection, tenant_id: str, category_key: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT profile_json
            FROM learning_profiles
            WHERE tenant_id=? AND category_key=?
            """,
            (tenant_id, category_key),
        ).fetchone()
        if row:
            return json.loads(row[0])
        return {
            "enabled": False,
            "version": 0,
            "approved_guidance": {},
            "updated_at": None,
        }

    def _upsert_recommendations(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        video_id: str,
        run: dict[str, Any],
        category_key: str,
        recommendations: list[dict[str, Any]],
    ) -> None:
        now = time.time()
        for recommendation in recommendations:
            rec_id = recommendation_id(tenant_id, video_id, category_key, recommendation)
            conn.execute(
                """
                INSERT INTO recommendations (
                  id, tenant_id, video_id, slug, category_key, created_at,
                  updated_at, status, recommendation_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(id) DO UPDATE SET
                  updated_at=excluded.updated_at,
                  recommendation_json=excluded.recommendation_json
                """,
                (
                    rec_id,
                    tenant_id,
                    video_id,
                    run.get("slug"),
                    category_key,
                    now,
                    now,
                    json.dumps(recommendation, ensure_ascii=False),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS latest_video_metrics (
                  tenant_id TEXT NOT NULL,
                  video_id TEXT NOT NULL,
                  slug TEXT,
                  category_key TEXT,
                  collected_at REAL NOT NULL,
                  metrics_json TEXT NOT NULL,
                  score_json TEXT NOT NULL,
                  diagnosis_json TEXT NOT NULL,
                  creative_json TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, video_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tenant_id TEXT NOT NULL,
                  video_id TEXT NOT NULL,
                  slug TEXT,
                  category_key TEXT,
                  collected_at REAL NOT NULL,
                  metrics_json TEXT NOT NULL,
                  score_json TEXT NOT NULL,
                  diagnosis_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retention_points (
                  tenant_id TEXT NOT NULL,
                  video_id TEXT NOT NULL,
                  elapsed_ratio REAL NOT NULL,
                  audience_watch_ratio REAL,
                  relative_retention_performance REAL,
                  collected_at REAL NOT NULL,
                  PRIMARY KEY (tenant_id, video_id, elapsed_ratio)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendations (
                  id TEXT PRIMARY KEY,
                  tenant_id TEXT NOT NULL,
                  video_id TEXT NOT NULL,
                  slug TEXT,
                  category_key TEXT,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL,
                  status TEXT NOT NULL,
                  recommendation_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_profiles (
                  tenant_id TEXT NOT NULL,
                  category_key TEXT NOT NULL,
                  updated_at REAL NOT NULL,
                  profile_json TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, category_key)
                )
                """
            )


def build_creative_manifest(
    *,
    spec: JobSpec,
    category: dict[str, Any],
    policy: dict[str, Any],
    timeline_manifest: dict[str, Any],
    metadata: VideoMetadata,
    image_files: list[Path],
    thumbnail: Path,
    learning_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timeline = timeline_manifest.get("timeline") or []
    new_count = sum(1 for item in timeline if item.get("is_new"))
    reused_count = sum(1 for item in timeline if item.get("is_reused"))
    extended_count = sum(1 for item in timeline if item.get("is_extended"))
    return {
        "schema_version": 1,
        "created_at": time.time(),
        "job": {
            "slug": spec.job.slug,
            "title_seed": spec.job.title_seed,
            "language": spec.job.language,
            "target_minutes": spec.job.target_minutes,
            "target_seconds": spec.job.target_seconds,
            "source_schedule_id": _env_or_none("YMF_SCHEDULE_ID"),
            "source_schedule_name": _env_or_none("YMF_SCHEDULE_NAME"),
        },
        "category_key": spec.category_key,
        "category": {
            "label": category.get("label"),
            "music_prompt": category.get("music_prompt"),
            "image_prompt": category.get("image_prompt"),
            "primary_keywords": category.get("primary_keywords") or [],
            "tags": category.get("tags") or [],
        },
        "channel_style": asdict(spec.channel_style),
        "providers": {
            "music": spec.music.provider,
            "music_model": spec.music.model,
            "image": spec.images.provider,
            "seo": spec.seo.provider,
        },
        "effective_prompts": {
            "music": spec.music.prompt or category.get("music_prompt") or "",
            "image": spec.images.prompt or category.get("image_prompt") or "",
            "seo_keyword": spec.seo.primary_keyword,
        },
        "asset_strategy": policy,
        "timeline_summary": {
            "total_items": len(timeline),
            "new_count": new_count,
            "reused_count": reused_count,
            "extended_count": extended_count,
            "fresh_generated_minutes": timeline_manifest.get("fresh_generated_minutes"),
            "reused_minutes": timeline_manifest.get("reused_minutes"),
            "internally_extended_minutes": timeline_manifest.get("internally_extended_minutes"),
            "reuse_ratio": timeline_manifest.get("reuse_ratio"),
            "extension_ratio": timeline_manifest.get("extension_ratio"),
        },
        "timeline": timeline,
        "metadata": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "category_id": metadata.category_id,
            "privacy_status": metadata.privacy_status,
        },
        "visuals": {
            "image_files": [str(path) for path in image_files],
            "thumbnail": str(thumbnail),
            "image_count": len(image_files),
        },
        "learning_profile": learning_profile or {},
    }


def write_creative_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    write_json(path, manifest)
    return path


def load_creative_manifest(run: dict[str, Any]) -> dict[str, Any]:
    path = run.get("creative_manifest_path") or run.get("creative_manifest")
    if path and Path(str(path)).exists():
        try:
            return json.loads(Path(str(path)).read_text(encoding="utf-8"))
        except Exception:
            return {}
    job_dir = run.get("job_dir")
    if job_dir:
        candidate = Path(str(job_dir)) / "creative_manifest.json"
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def summarize_creative_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    category = manifest.get("category") or {}
    metadata = manifest.get("metadata") or {}
    timeline_summary = manifest.get("timeline_summary") or {}
    return {
        "job": manifest.get("job") or {},
        "category_key": manifest.get("category_key"),
        "category_label": category.get("label"),
        "music_prompt": category.get("music_prompt"),
        "image_prompt": category.get("image_prompt"),
        "thumbnail_style": (manifest.get("channel_style") or {}).get("thumbnail_style"),
        "visual_style": (manifest.get("channel_style") or {}).get("visual_style"),
        "title": metadata.get("title"),
        "tags": metadata.get("tags") or [],
        "timeline_summary": timeline_summary,
    }


def apply_learning_profile_to_spec(
    *,
    spec: JobSpec,
    category: dict[str, Any],
    db_path: Path,
    channel_id: str,
) -> dict[str, Any]:
    store = AnalyticsStore(db_path)
    profile = store.load_learning_profile(channel_id, spec.category_key)
    if not profile.get("enabled"):
        return profile
    guidance = profile.get("approved_guidance") or {}
    music_guidance = [str(item) for item in guidance.get("music") or [] if str(item).strip()]
    thumbnail_guidance = [str(item) for item in guidance.get("thumbnail") or [] if str(item).strip()]
    metadata_guidance = [str(item) for item in guidance.get("metadata") or [] if str(item).strip()]
    if music_guidance:
        base = spec.music.prompt or str(category.get("music_prompt") or "")
        spec.music.prompt = _append_guidance(base, "Performance learning guidance for music", music_guidance)
    if thumbnail_guidance:
        base = spec.images.prompt or str(category.get("image_prompt") or "")
        spec.images.prompt = _append_guidance(base, "Performance learning guidance for visuals", thumbnail_guidance)
    if metadata_guidance:
        existing = spec.channel_style.thumbnail_style or ""
        spec.channel_style.thumbnail_style = _append_guidance(existing, "Performance learning guidance for packaging", metadata_guidance)
    return profile


def calculate_performance_score(metrics: dict[str, Any], *, creative: dict[str, Any] | None = None) -> dict[str, Any]:
    creative = creative or {}
    views = _float(metrics.get("views"))
    impressions = _float(metrics.get("videoThumbnailImpressions"))
    watch_minutes = _float(metrics.get("estimatedMinutesWatched"))
    avg_view_duration = _float(metrics.get("averageViewDuration"))
    target_seconds = _target_seconds(creative)
    ctr = _as_ratio(metrics.get("videoThumbnailImpressionsClickRate"))
    avg_view_percentage = _as_ratio(metrics.get("averageViewPercentage"))
    if not avg_view_percentage and target_seconds:
        avg_view_percentage = max(0.0, min(1.0, avg_view_duration / target_seconds))
    watch_minutes_per_impression = watch_minutes / impressions if impressions else 0.0
    likes = _float(metrics.get("likes"))
    comments = _float(metrics.get("comments"))
    shares = _float(metrics.get("shares"))
    subscribers_gained = _float(metrics.get("subscribersGained"))
    subscribers_lost = _float(metrics.get("subscribersLost"))
    estimated_revenue = _float(metrics.get("estimatedRevenue"))

    ctr_score = min(1.0, ctr / 0.08) if ctr else 0.0
    retention_score = min(1.0, avg_view_percentage / 0.35) if avg_view_percentage else 0.0
    watch_score = min(1.0, watch_minutes_per_impression / 1.2) if watch_minutes_per_impression else 0.0
    engagement_rate = (likes + comments * 3 + shares * 2) / max(views, 1.0)
    engagement_score = min(1.0, engagement_rate / 0.08)
    subscriber_rate = max(0.0, subscribers_gained - subscribers_lost) / max(views, 1.0)
    subscriber_score = min(1.0, subscriber_rate / 0.02)
    revenue_score = min(1.0, (estimated_revenue / max(views, 1.0)) / 0.01) if estimated_revenue else 0.0
    weighted = (
        watch_score * 0.30
        + ctr_score * 0.25
        + retention_score * 0.20
        + subscriber_score * 0.10
        + engagement_score * 0.10
        + revenue_score * 0.05
    )
    confidence = min(1.0, max(impressions / 500.0, views / 100.0, _age_days(metrics, creative) / 7.0))
    performance_score = round(100 * weighted * (0.75 + 0.25 * confidence), 1)
    return {
        "performance_score": performance_score,
        "confidence": round(confidence, 3),
        "components": {
            "watch_time_per_impression": round(watch_score, 3),
            "thumbnail_ctr": round(ctr_score, 3),
            "retention": round(retention_score, 3),
            "subscriber_gain": round(subscriber_score, 3),
            "engagement": round(engagement_score, 3),
            "revenue": round(revenue_score, 3),
        },
        "raw": {
            "ctr": round(ctr, 4),
            "average_view_percentage": round(avg_view_percentage, 4),
            "watch_minutes_per_impression": round(watch_minutes_per_impression, 4),
            "engagement_rate": round(engagement_rate, 4),
            "subscriber_rate": round(subscriber_rate, 4),
        },
    }


def diagnose_performance(
    metrics: dict[str, Any],
    score: dict[str, Any],
    *,
    creative: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctr = score.get("raw", {}).get("ctr") or 0
    retention = score.get("raw", {}).get("average_view_percentage") or 0
    impressions = _float(metrics.get("videoThumbnailImpressions"))
    confidence = _float(score.get("confidence"))
    labels: list[str] = []
    summary = "Performance data is still warming up."
    if confidence < 0.35:
        labels.append("needs_more_data")
    if ctr >= 0.06 and retention < 0.18:
        summary = "Strong packaging, weak content-retention match."
        labels.extend(["strong_packaging", "retention_gap"])
    elif ctr < 0.03 and retention >= 0.25:
        summary = "Content retains viewers, but title/thumbnail under-convert."
        labels.extend(["packaging_gap", "strong_content"])
    elif impressions < 500 and score.get("performance_score", 0) >= 55:
        summary = "Promising video with limited distribution so far."
        labels.extend(["limited_distribution", "promising"])
    elif score.get("performance_score", 0) >= 70:
        summary = "Winner candidate. Preserve the core creative pattern."
        labels.append("winner")
    elif retention < 0.12 and _float(metrics.get("views")) >= 50:
        summary = "Early retention is weak. Intro, first clip, or expectation match likely needs work."
        labels.append("weak_retention")
    elif ctr < 0.03:
        summary = "Thumbnail/title likely need sharper positioning."
        labels.append("weak_ctr")
    elif not labels:
        summary = "Middle performer. Use as baseline, not as a winner."
        labels.append("baseline")
    return {
        "summary": summary,
        "labels": sorted(set(labels)),
        "confidence": "high" if confidence >= 0.75 else "medium" if confidence >= 0.35 else "low",
    }


def recommend_actions(
    metrics: dict[str, Any],
    score: dict[str, Any],
    diagnosis: dict[str, Any],
    *,
    creative: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    creative = creative or {}
    labels = set(diagnosis.get("labels") or [])
    category_key = str(creative.get("category_key") or "")
    recommendations: list[dict[str, Any]] = []
    if "winner" in labels or score.get("performance_score", 0) >= 70:
        recommendations.append(
            {
                "kind": "preserve_pattern",
                "target": "music",
                "title": "Preserve the winning music pattern",
                "guidance": "Keep the core tempo, mood, density, and instrumental restraint from this category prompt.",
                "category_key": category_key,
                "priority": "high",
            }
        )
        recommendations.append(
            {
                "kind": "preserve_packaging",
                "target": "thumbnail",
                "title": "Reuse the winning thumbnail direction",
                "guidance": "Keep the same visual contrast, subject density, and thumbnail composition family.",
                "category_key": category_key,
                "priority": "medium",
            }
        )
    if "packaging_gap" in labels or "weak_ctr" in labels:
        recommendations.append(
            {
                "kind": "improve_ctr",
                "target": "thumbnail",
                "title": "Increase thumbnail/title contrast",
                "guidance": "Use a clearer focal shape, stronger contrast, and simpler title positioning for this category.",
                "category_key": category_key,
                "priority": "high",
            }
        )
    if "retention_gap" in labels or "weak_retention" in labels:
        recommendations.append(
            {
                "kind": "improve_retention",
                "target": "music",
                "title": "Reduce intro friction",
                "guidance": "Start with a steadier groove earlier, avoid long sparse intros, and keep first 3 minutes aligned with the title promise.",
                "category_key": category_key,
                "priority": "high",
            }
        )
    if "limited_distribution" in labels:
        recommendations.append(
            {
                "kind": "improve_distribution",
                "target": "metadata",
                "title": "Test broader SEO phrasing",
                "guidance": "Keep the content style, but test a broader keyword/title phrase for the next run.",
                "category_key": category_key,
                "priority": "medium",
            }
        )
    return recommendations[:5]


def sync_youtube_analytics(
    *,
    db_path: Path,
    tenant_id: str,
    runs: list[dict[str, Any]],
    token_file: Path,
    include_monetary: bool = False,
    days: int = 90,
) -> dict[str, Any]:
    video_runs = [run for run in runs if run.get("youtube_video_id")]
    if not video_runs:
        return {"synced": 0, "summary": AnalyticsStore(db_path).summary(tenant_id=tenant_id, runs=runs)}
    if not token_file.exists():
        raise FileNotFoundError(f"YouTube token not found: {token_file}")
    video_ids = [str(run["youtube_video_id"]) for run in video_runs]
    youtube, analytics = _build_youtube_services(token_file, include_monetary=include_monetary)
    end_date = date.today()
    start_date = end_date - timedelta(days=max(1, days))
    stats = _fetch_video_statistics(youtube, video_ids)
    aggregate = _fetch_video_analytics(analytics, video_ids, start_date, end_date, include_monetary=include_monetary)
    retention = _fetch_retention(analytics, video_ids, start_date, end_date)
    store = AnalyticsStore(db_path)
    synced = 0
    for run in video_runs:
        video_id = str(run["youtube_video_id"])
        metrics = {
            "video_id": video_id,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "collected_at": time.time(),
            **stats.get(video_id, {}),
            **aggregate.get(video_id, {}),
        }
        store.upsert_video_performance(
            tenant_id=tenant_id,
            run=run,
            metrics=metrics,
            retention_points=retention.get(video_id, []),
        )
        synced += 1
    return {"synced": synced, "summary": store.summary(tenant_id=tenant_id, runs=runs)}


def recommendation_id(tenant_id: str, video_id: str, category_key: str, recommendation: dict[str, Any]) -> str:
    seed = json.dumps(
        {
            "tenant_id": tenant_id,
            "video_id": video_id,
            "category_key": category_key,
            "kind": recommendation.get("kind"),
            "target": recommendation.get("target"),
            "guidance": recommendation.get("guidance"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "rec_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _build_youtube_services(token_file: Path, *, include_monetary: bool):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Google API client libraries are required for analytics sync") from exc

    scopes = [YOUTUBE_READONLY_SCOPE, YOUTUBE_ANALYTICS_SCOPE]
    if include_monetary:
        scopes.append(YOUTUBE_MONETARY_ANALYTICS_SCOPE)
    creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds), build("youtubeAnalytics", "v2", credentials=creds)


def _fetch_video_statistics(service, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(video_ids, 50):
        response = service.videos().list(
            part="snippet,statistics,contentDetails,status",
            id=",".join(chunk),
        ).execute()
        for item in response.get("items", []):
            video_id = item.get("id")
            stats = item.get("statistics") or {}
            snippet = item.get("snippet") or {}
            result[str(video_id)] = {
                "views": _float(stats.get("viewCount")),
                "likes": _float(stats.get("likeCount")),
                "comments": _float(stats.get("commentCount")),
                "publishedAt": snippet.get("publishedAt"),
                "youtubeTitle": snippet.get("title"),
                "youtubeTags": snippet.get("tags") or [],
            }
    return result


def _fetch_video_analytics(
    service,
    video_ids: list[str],
    start_date: date,
    end_date: date,
    *,
    include_monetary: bool,
) -> dict[str, dict[str, Any]]:
    metrics = list(CORE_ANALYTICS_METRICS)
    if include_monetary:
        metrics.append("estimatedRevenue")
    result = _query_video_dimension_report(service, video_ids, start_date, end_date, metrics)
    reach = _query_video_dimension_report(service, video_ids, start_date, end_date, REACH_METRICS, soft=True)
    for video_id, payload in reach.items():
        result.setdefault(video_id, {}).update(payload)
    return result


def _query_video_dimension_report(
    service,
    video_ids: list[str],
    start_date: date,
    end_date: date,
    metrics: list[str],
    *,
    soft: bool = False,
) -> dict[str, dict[str, Any]]:
    try:
        response = service.reports().query(
            ids="channel==MINE",
            startDate=start_date.isoformat(),
            endDate=end_date.isoformat(),
            metrics=",".join(metrics),
            dimensions="video",
            filters="video==" + ",".join(video_ids[:500]),
        ).execute()
    except Exception:
        if not soft:
            raise
        return {}
    headers = [header.get("name") for header in response.get("columnHeaders", [])]
    rows = response.get("rows") or []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(zip(headers, row, strict=False))
        video_id = str(payload.pop("video", ""))
        if video_id:
            result[video_id] = {key: _float(value) for key, value in payload.items()}
    return result


def _fetch_retention(
    service,
    video_ids: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for video_id in video_ids:
        try:
            response = service.reports().query(
                ids="channel==MINE",
                startDate=start_date.isoformat(),
                endDate=end_date.isoformat(),
                metrics=",".join(RETENTION_METRICS),
                dimensions="elapsedVideoTimeRatio",
                filters=f"video=={video_id}",
            ).execute()
        except Exception:
            continue
        headers = [header.get("name") for header in response.get("columnHeaders", [])]
        rows = response.get("rows") or []
        points = []
        for row in rows:
            payload = dict(zip(headers, row, strict=False))
            points.append({key: _float(value) for key, value in payload.items()})
        result[video_id] = points
    return result


def _category_breakdown(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for video in videos:
        grouped.setdefault(str(video.get("category_key") or "unknown"), []).append(video)
    rows = []
    for category_key, items in grouped.items():
        scores = [float(item.get("score", {}).get("performance_score") or 0) for item in items]
        views = [_float(item.get("metrics", {}).get("views")) for item in items]
        ctrs = [float(item.get("score", {}).get("raw", {}).get("ctr") or 0) for item in items]
        retentions = [float(item.get("score", {}).get("raw", {}).get("average_view_percentage") or 0) for item in items]
        rows.append(
            {
                "category_key": category_key,
                "video_count": len(items),
                "average_score": round(sum(scores) / max(1, len(scores)), 1),
                "total_views": int(sum(views)),
                "average_ctr": round(sum(ctrs) / max(1, len(ctrs)), 4),
                "average_retention": round(sum(retentions) / max(1, len(retentions)), 4),
            }
        )
    return sorted(rows, key=lambda item: item["average_score"], reverse=True)


def _append_guidance(base: str, title: str, items: list[str]) -> str:
    clean_base = (base or "").strip()
    guidance = "\n".join(f"- {item.strip()}" for item in items if item.strip())
    if not guidance:
        return clean_base
    block = f"{title}:\n{guidance}"
    return f"{clean_base}\n\n{block}".strip() if clean_base else block


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def _target_seconds(creative: dict[str, Any]) -> float:
    job = creative.get("job") or {}
    return _float(job.get("target_seconds")) or _float(job.get("target_minutes")) * 60 or 3600.0


def _age_days(metrics: dict[str, Any], creative: dict[str, Any]) -> float:
    published = metrics.get("publishedAt")
    if published:
        try:
            published_dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            return max(0.0, (datetime.now(timezone.utc) - published_dt).total_seconds() / 86400)
        except ValueError:
            pass
    created_at = _float((creative.get("job") or {}).get("created_at") or creative.get("created_at"))
    if created_at:
        return max(0.0, (time.time() - created_at) / 86400)
    return 0.0


def _as_ratio(value: Any) -> float:
    numeric = _float(value)
    if numeric > 1.0:
        return numeric / 100.0
    return numeric


def _float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_or_none(key: str) -> str | None:
    value = os.environ.get(key)
    return value or None
