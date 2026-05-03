from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .ffmpeg import ffprobe_duration
from .utils import ensure_dir


class AssetRegistry:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        ensure_dir(db_path.parent)
        self._init_db()

    def register_track(self, track_metadata: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(track_metadata)
        metadata.setdefault("id", track_id_for_file(Path(metadata["source_file"])))
        metadata.setdefault("generated_at", time.time())
        metadata.setdefault("usage_count_global", 0)
        metadata.setdefault("usage_count_by_channel", {})
        metadata.setdefault("used_in_video_ids", [])
        metadata.setdefault("last_used_slot_indexes", [])
        metadata.setdefault("extended_usage_count", 0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tracks (
                  id, provider, model, source_file, duration_seconds, generated_at,
                  channel_id, channel_theme, substyle, reusable, loop_safe, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  source_file=excluded.source_file,
                  duration_seconds=excluded.duration_seconds,
                  metadata_json=excluded.metadata_json
                """,
                (
                    metadata["id"],
                    metadata.get("provider"),
                    metadata.get("model"),
                    metadata.get("source_file"),
                    float(metadata.get("duration_seconds") or 0),
                    float(metadata.get("generated_at") or time.time()),
                    metadata.get("channel_id"),
                    metadata.get("channel_theme"),
                    metadata.get("substyle"),
                    1 if metadata.get("reusable", True) else 0,
                    1 if metadata.get("loop_safe", True) else 0,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
        return metadata

    def find_reusable_tracks(self, theme: str, substyle: str, policy: dict[str, Any], exclude_rules: dict | None = None) -> list[dict[str, Any]]:
        exclude_rules = exclude_rules or {}
        eligibility = policy.get("eligibility", {}).get("reusable_only_if", {})
        reuse_policy = policy.get("cross_video_reuse", {})
        min_age_days = float(reuse_policy.get("min_days_before_reuse_same_channel") or 0)
        min_videos = int(reuse_policy.get("min_videos_before_reuse_same_channel") or 0)
        max_same_channel = int(reuse_policy.get("max_uses_same_channel_lifetime") or 0)
        max_global = int(reuse_policy.get("max_uses_global_lifetime") or 0)
        max_global_30_days = int(reuse_policy.get("max_uses_global_30_days") or 0)
        channel_id = str(exclude_rules.get("channel_id") or "")
        history = list(exclude_rules.get("history") or [])
        now = time.time()
        excluded_ids = set(exclude_rules.get("track_ids") or [])
        with self._connect() as conn:
            rows = conn.execute("SELECT metadata_json FROM tracks WHERE reusable=1").fetchall()
        tracks = [json.loads(row[0]) for row in rows]
        result = []
        for track in tracks:
            if track.get("id") in excluded_ids:
                continue
            if not Path(str(track.get("source_file") or "")).exists():
                continue
            if theme and track.get("channel_theme") not in {theme, None, ""}:
                continue
            if substyle and track.get("substyle") not in {substyle, None, ""}:
                continue
            if not _eligible(track, eligibility):
                continue
            last_used = float(track.get("last_used_at") or 0)
            if last_used and (now - last_used) < min_age_days * 86400:
                continue
            by_channel = dict(track.get("usage_count_by_channel") or {})
            if max_same_channel and channel_id and int(by_channel.get(channel_id) or 0) >= max_same_channel:
                continue
            if max_global and int(track.get("usage_count_global") or 0) >= max_global:
                continue
            usage_timestamps = [float(ts) for ts in track.get("usage_timestamps_global") or []]
            if max_global_30_days and sum(1 for ts in usage_timestamps if now - ts <= 30 * 86400) >= max_global_30_days:
                continue
            if min_videos and channel_id and not _enough_videos_since_use(track, history, min_videos):
                continue
            result.append(track)
        result.sort(key=lambda item: (item.get("usage_count_global") or 0, item.get("last_used_at") or 0))
        return result

    def update_usage_after_render(self, video_manifest: dict[str, Any]) -> None:
        video_id = str(video_manifest.get("job_id") or video_manifest.get("video_id"))
        channel_id = str(video_manifest.get("channel_id") or "default")
        now = float(video_manifest.get("created_at") or time.time())
        timeline = video_manifest.get("timeline") or []
        with self._connect() as conn:
            for item in timeline:
                track_id = item.get("track_id")
                if not track_id:
                    continue
                row = conn.execute("SELECT metadata_json FROM tracks WHERE id=?", (track_id,)).fetchone()
                if not row:
                    continue
                metadata = json.loads(row[0])
                metadata["usage_count_global"] = int(metadata.get("usage_count_global") or 0) + 1
                by_channel = dict(metadata.get("usage_count_by_channel") or {})
                by_channel[channel_id] = int(by_channel.get(channel_id) or 0) + 1
                metadata["usage_count_by_channel"] = by_channel
                used_in = list(metadata.get("used_in_video_ids") or [])
                if video_id not in used_in:
                    used_in.append(video_id)
                metadata["used_in_video_ids"] = used_in
                metadata["last_used_at"] = now
                slots = list(metadata.get("last_used_slot_indexes") or [])
                slots.append(int(item.get("slot_index") or 0))
                metadata["last_used_slot_indexes"] = slots[-50:]
                slot_entries = list(metadata.get("last_used_slot_entries") or [])
                slot_entries.append({"slot_index": int(item.get("slot_index") or 0), "used_at": now, "channel_id": channel_id})
                metadata["last_used_slot_entries"] = slot_entries[-100:]
                usage_timestamps = list(metadata.get("usage_timestamps_global") or [])
                usage_timestamps.append(now)
                metadata["usage_timestamps_global"] = usage_timestamps[-200:]
                usage_by_channel = dict(metadata.get("usage_timestamps_by_channel") or {})
                channel_timestamps = list(usage_by_channel.get(channel_id) or [])
                channel_timestamps.append(now)
                usage_by_channel[channel_id] = channel_timestamps[-100:]
                metadata["usage_timestamps_by_channel"] = usage_by_channel
                if item.get("is_extended"):
                    metadata["extended_usage_count"] = int(metadata.get("extended_usage_count") or 0) + 1
                conn.execute(
                    "UPDATE tracks SET metadata_json=? WHERE id=?",
                    (json.dumps(metadata, ensure_ascii=False), track_id),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO videos (id, channel_id, theme, created_at, manifest_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    channel_id,
                    video_manifest.get("theme"),
                    now,
                    json.dumps(video_manifest, ensure_ascii=False),
                ),
            )

    def get_video_history(self, channel_id: str, theme: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if theme:
                rows = conn.execute(
                    "SELECT manifest_json FROM videos WHERE channel_id=? AND theme=? ORDER BY created_at DESC",
                    (channel_id, theme),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT manifest_json FROM videos WHERE channel_id=? ORDER BY created_at DESC",
                    (channel_id,),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_other_video_history(self, channel_id: str, theme: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if theme:
                rows = conn.execute(
                    "SELECT manifest_json FROM videos WHERE channel_id<>? AND theme=? ORDER BY created_at DESC",
                    (channel_id, theme),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT manifest_json FROM videos WHERE channel_id<>? ORDER BY created_at DESC",
                    (channel_id,),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_clip_usage(self, track_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT metadata_json FROM tracks WHERE id=?", (track_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                  id TEXT PRIMARY KEY,
                  provider TEXT,
                  model TEXT,
                  source_file TEXT,
                  duration_seconds REAL,
                  generated_at REAL,
                  channel_id TEXT,
                  channel_theme TEXT,
                  substyle TEXT,
                  reusable INTEGER,
                  loop_safe INTEGER,
                  metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                  id TEXT PRIMARY KEY,
                  channel_id TEXT,
                  theme TEXT,
                  created_at REAL,
                  manifest_json TEXT NOT NULL
                )
                """
            )


def track_id_for_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    return f"trk_{digest}"


def build_track_metadata(
    *,
    source_file: Path,
    provider: str,
    model: str | None,
    channel_id: str,
    channel_theme: str,
    substyle: str,
    prompt: str,
) -> dict[str, Any]:
    duration = ffprobe_duration(source_file)
    fingerprint = hashlib.sha256(source_file.read_bytes()).hexdigest()
    return {
        "id": f"trk_{fingerprint[:20]}",
        "provider": provider,
        "model": model or "",
        "source_file": str(source_file),
        "duration_seconds": duration,
        "generated_at": time.time(),
        "channel_id": channel_id,
        "channel_theme": channel_theme,
        "substyle": substyle,
        "bpm": None,
        "key": None,
        "energy": "medium",
        "mood_tags": [],
        "instrument_tags": [],
        "reusable": True,
        "loop_safe": True,
        "hook_strength": "low",
        "vocal_presence": "none",
        "strong_drop": False,
        "strong_intro": False,
        "strong_outro": False,
        "final_cadence": False,
        "audio_fingerprint": fingerprint,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "usage_count_global": 0,
        "usage_count_by_channel": {},
        "used_in_video_ids": [],
        "last_used_at": None,
        "last_used_slot_indexes": [],
        "last_used_slot_entries": [],
        "usage_timestamps_global": [],
        "usage_timestamps_by_channel": {},
        "extended_usage_count": 0,
    }


def _eligible(track: dict[str, Any], eligibility: dict[str, Any]) -> bool:
    if eligibility.get("reusable") is True and not track.get("reusable", True):
        return False
    if eligibility.get("loop_safe") is True and not track.get("loop_safe", True):
        return False
    if eligibility.get("vocal_presence_allowed") is False and track.get("vocal_presence") not in {None, "", "none"}:
        return False
    for key in ("strong_drop", "strong_intro", "strong_outro", "final_cadence"):
        if eligibility.get(f"{key}_allowed") is False and track.get(key):
            return False
    if not _hook_strength_allowed(str(track.get("hook_strength") or "low"), str(eligibility.get("hook_strength_max") or "high")):
        return False
    return True


def _hook_strength_allowed(value: str, maximum: str) -> bool:
    order = {"low": 0, "medium": 1, "high": 2}
    return order.get(value, 2) <= order.get(maximum, 2)


def _enough_videos_since_use(track: dict[str, Any], history: list[dict[str, Any]], min_videos: int) -> bool:
    used_in = set(track.get("used_in_video_ids") or [])
    if not used_in:
        return True
    for idx, video in enumerate(history):
        if str(video.get("job_id") or video.get("video_id")) in used_in:
            return idx >= min_videos
    return True
