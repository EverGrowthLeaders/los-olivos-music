from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import JobSpec
from .utils import unique_ordered

YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_TAGS_SOFT_LIMIT = 450


@dataclass(slots=True)
class VideoChapter:
    start_seconds: int
    title: str


@dataclass(slots=True)
class VideoMetadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    language: str
    contains_synthetic_media: bool
    made_for_kids: bool
    privacy_status: str
    notify_subscribers: bool
    publish_at: str | None = None
    chapters: list[VideoChapter] | None = None

    def to_upload_body(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "privacyStatus": self.privacy_status,
            "selfDeclaredMadeForKids": self.made_for_kids,
            "containsSyntheticMedia": self.contains_synthetic_media,
            "embeddable": True,
            "license": "youtube",
            "publicStatsViewable": True,
        }
        if self.publish_at:
            status["publishAt"] = self.publish_at
            status["privacyStatus"] = "private"
        return {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": self.category_id,
                "defaultLanguage": self.language,
            },
            "status": status,
        }

    def to_json(self) -> dict[str, Any]:
        payload = self.to_upload_body()
        payload["notifySubscribers"] = self.notify_subscribers
        payload["chapters"] = [
            {"start_seconds": chapter.start_seconds, "title": chapter.title}
            for chapter in self.chapters or []
        ]
        return payload


def clamp_title(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= YOUTUBE_TITLE_LIMIT:
        return value
    return value[: YOUTUBE_TITLE_LIMIT - 1].rstrip(" |,-") + "…"


def _build_title(spec: JobSpec, category: dict[str, Any], primary_keyword: str) -> str:
    phrase = (category.get("title_phrases") or [category.get("label", "AI Music Mix")])[0]
    seed = f" — {spec.job.title_seed.title()}" if spec.job.title_seed else ""
    target = int(round(spec.job.target_minutes)) if spec.job.target_minutes >= 1 else spec.job.target_minutes
    if spec.job.target_minutes >= 55:
        duration = "1 Hour"
    else:
        duration = f"{target} Min"
    title = f"{duration} {phrase}{seed} | {primary_keyword}"
    return clamp_title(title)


def _format_timestamp(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _format_chapters(chapters: list[VideoChapter] | None) -> str:
    if not chapters:
        return ""
    return "\n".join(f"{_format_timestamp(chapter.start_seconds)} {chapter.title}" for chapter in chapters)


def _tracklist(track_count: int, track_duration_seconds: int, target_seconds: int) -> str:
    if track_count <= 0:
        return ""
    lines = []
    elapsed = 0
    idx = 1
    while elapsed < target_seconds and idx <= max(track_count, 1):
        lines.append(f"{_format_timestamp(elapsed)} Original AI-assisted track {idx:02d}")
        elapsed += max(1, track_duration_seconds)
        idx += 1
    return "\n".join(lines)


def _bounded_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    total = 0
    for tag in unique_ordered(tags):
        clean = tag[:60].strip()
        if not clean:
            continue
        projected = total + len(clean) + 1
        if projected > YOUTUBE_TAGS_SOFT_LIMIT:
            break
        result.append(clean)
        total = projected
    return result


def _with_chapters(description: str, chapters: list[VideoChapter] | None) -> str:
    chapter_text = _format_chapters(chapters)
    if not chapter_text:
        return description.strip()
    description = re.sub(
        r"\n\n(?:Chapters|Tracklist):\n(?:\d{1,2}:\d{2}(?::\d{2})?.*(?:\n|$))+",
        "\n",
        description.strip(),
        flags=re.I,
    ).strip()
    return f"{description.strip()}\n\nChapters:\n{chapter_text}".strip()


def build_local_metadata(
    spec: JobSpec,
    category: dict[str, Any],
    *,
    chapters: list[VideoChapter] | None = None,
) -> VideoMetadata:
    primary_keyword = spec.seo.primary_keyword or (category.get("primary_keywords") or ["ai music"])[0]
    title = _build_title(spec, category, primary_keyword)
    category_id = spec.youtube.category_id or str(category.get("youtube_category_id") or "10")
    tags = _bounded_tags(
        [primary_keyword]
        + list(category.get("primary_keywords") or [])
        + list(category.get("tags") or [])
        + [spec.category_key.replace("_", " "), "original music", "background music"]
    )
    tracklist = _format_chapters(chapters) or _tracklist(
        spec.music.track_count,
        spec.music.track_duration_seconds,
        spec.job.target_seconds,
    )
    provider_note = ""
    if spec.music.provider == "elevenlabs":
        provider_note = "\nMusic attribution: Created in collaboration with ElevenLabs."
    elif spec.music.provider == "mubert":
        provider_note = "\nMusic generated/licensed through the configured Mubert API plan."

    disclosure = (
        "\nDisclosure: this video uses synthetically generated or AI-assisted music and "
        "AI-assisted background artwork."
        if spec.youtube.contains_synthetic_media
        else ""
    )

    value = category.get("audience_value", "focused listening")
    description = f"""{title}

Original long-form music mix for {value}.

Use it as background music for work, study, relaxation, creative sessions, or calm ambience.

Chapters:
{tracklist or "Continuous original mix"}

Visuals: AI-assisted still artwork generated for this upload.
Audio: original AI-assisted music generated for this upload.{provider_note}{disclosure}

No artist voice, song title, label, or copyrighted lyric references were intentionally used in the prompts.

#aimusic #{spec.category_key.replace('_', '')} #backgroundmusic
""".strip()

    return VideoMetadata(
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        language=spec.job.language,
        contains_synthetic_media=spec.youtube.contains_synthetic_media,
        made_for_kids=spec.youtube.made_for_kids,
        privacy_status=spec.youtube.privacy_status,
        notify_subscribers=spec.youtube.notify_subscribers,
        publish_at=spec.youtube.publish_at,
        chapters=chapters,
    )


def build_gemini_metadata(
    spec: JobSpec,
    category: dict[str, Any],
    *,
    chapters: list[VideoChapter] | None = None,
) -> VideoMetadata:
    """Generate metadata via Gemini, then enforce local safety and size constraints.

    The local template is used as a fallback and as a guardrail so a bad model output cannot break
    YouTube upload constraints.
    """
    fallback = build_local_metadata(spec, category, chapters=chapters)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fallback

    model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite-preview")
    prompt = {
        "task": "Create YouTube SEO metadata for an original AI-assisted long-form music video.",
        "constraints": [
            "Return strict JSON only with keys: title, description, tags.",
            "Title must be <= 100 characters.",
            "Tags must be an array of short YouTube tags; no artist names; no song titles; no label names.",
            "Description must include an AI/synthetic music disclosure.",
            "Keep the supplied chapter timestamps exactly if chapters are provided.",
            "Do not claim the music is human-composed or copyright-free unless the license is verified separately.",
        ],
        "category": category,
        "job": {
            "slug": spec.job.slug,
            "title_seed": spec.job.title_seed,
            "language": spec.job.language,
            "target_minutes": spec.job.target_minutes,
        },
        "primary_keyword": spec.seo.primary_keyword,
        "music_provider": spec.music.provider,
        "chapters": [
            {"timestamp": _format_timestamp(chapter.start_seconds), "title": chapter.title}
            for chapter in chapters or []
        ],
        "channel_style": {
            "aesthetic": spec.channel_style.aesthetic,
            "visual_style": spec.channel_style.visual_style,
            "color_palette": spec.channel_style.color_palette,
            "sonic_identity": spec.channel_style.sonic_identity,
            "avoid": spec.channel_style.avoid,
        },
    }
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}]},
            timeout=120,
        )
        response.raise_for_status()
        text = _extract_gemini_text(response.json())
        candidate = _parse_json_object(text)
        title = clamp_title(str(candidate.get("title") or fallback.title))
        description = _with_chapters(str(candidate.get("description") or fallback.description), chapters)
        if "synthetic" not in description.lower() and "ai" not in description.lower():
            description += "\n\nDisclosure: this video uses synthetically generated or AI-assisted music."
        tags = _bounded_tags([str(t) for t in candidate.get("tags", [])] + fallback.tags)
        return VideoMetadata(
            title=title,
            description=description[:4900],
            tags=tags or fallback.tags,
            category_id=fallback.category_id,
            language=fallback.language,
            contains_synthetic_media=fallback.contains_synthetic_media,
            made_for_kids=fallback.made_for_kids,
            privacy_status=fallback.privacy_status,
            notify_subscribers=fallback.notify_subscribers,
            publish_at=fallback.publish_at,
            chapters=chapters,
        )
    except Exception as exc:  # noqa: BLE001 - fallback is intentionally resilient
        print(f"Gemini SEO generation failed; using local metadata. Reason: {exc}")
        return fallback


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    parts = (candidates[0].get("content", {}).get("parts") if candidates else []) or []
    return "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        cleaned = match.group(0)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Gemini metadata response was not a JSON object")
    return parsed


def build_metadata(
    spec: JobSpec,
    category: dict[str, Any],
    *,
    chapters: list[VideoChapter] | None = None,
) -> VideoMetadata:
    if spec.seo.provider == "gemini":
        return build_gemini_metadata(spec, category, chapters=chapters)
    return build_local_metadata(spec, category, chapters=chapters)


def save_metadata(path: Path, metadata: VideoMetadata) -> Path:
    path.write_text(json.dumps(metadata.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
