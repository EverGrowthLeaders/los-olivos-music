from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .utils import project_root, safe_slug


@dataclass(slots=True)
class JobConfig:
    slug: str
    title_seed: str = ""
    language: str = "en"
    target_minutes: float = 60.0

    @property
    def target_seconds(self) -> int:
        return max(1, int(round(self.target_minutes * 60)))


@dataclass(slots=True)
class MusicConfig:
    provider: str = "local"
    prompt: str | None = None
    track_count: int = 12
    track_duration_seconds: int = 300
    instrumental: bool = True
    output_format: str = "mp3"
    model: str | None = None
    bitrate: int = 192
    intensity: str = "medium"
    mode: str = "track"


@dataclass(slots=True)
class ImageConfig:
    provider: str = "gemini"
    prompt: str | None = None
    count: int = 4
    aspect_ratio: str = "16:9"
    image_size: str = "2K"


@dataclass(slots=True)
class VideoConfig:
    resolution: str = "1920x1080"
    fps: int = 1
    visual_mode: str = "slideshow"
    image_duration_seconds: int = 900
    video_preset: str = "veryfast"
    audio_bitrate: str = "192k"
    normalize_audio: bool = False

    @property
    def width_height(self) -> tuple[int, int]:
        if "x" not in self.resolution:
            raise ValueError("video.resolution must look like '1920x1080'")
        width, height = self.resolution.lower().split("x", 1)
        return int(width), int(height)


@dataclass(slots=True)
class SeoConfig:
    provider: str = "local"
    primary_keyword: str | None = None


@dataclass(slots=True)
class ChannelStyleConfig:
    theme: str | None = None
    aesthetic: str | None = None
    visual_style: str | None = None
    color_palette: str | None = None
    sonic_identity: str | None = None
    avoid: str | None = None


@dataclass(slots=True)
class YoutubeConfig:
    upload: bool = False
    privacy_status: str = "private"
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    notify_subscribers: bool = False
    category_id: str | None = None
    publish_at: str | None = None
    set_thumbnail: bool = True


@dataclass(slots=True)
class AssetsConfig:
    audio_files: list[str] = field(default_factory=list)
    image_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobSpec:
    job: JobConfig
    category_key: str
    music: MusicConfig = field(default_factory=MusicConfig)
    images: ImageConfig = field(default_factory=ImageConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    seo: SeoConfig = field(default_factory=SeoConfig)
    channel_style: ChannelStyleConfig = field(default_factory=ChannelStyleConfig)
    youtube: YoutubeConfig = field(default_factory=YoutubeConfig)
    assets: AssetsConfig = field(default_factory=AssetsConfig)
    source_path: Path | None = None


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {}) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Section '{key}' must be a mapping")
    return value


def load_spec(path: Path) -> JobSpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Spec file must contain a YAML mapping")
    job_data = _section(raw, "job")
    slug = safe_slug(str(job_data.get("slug") or raw.get("category_key") or "job"))
    job = JobConfig(
        slug=slug,
        title_seed=str(job_data.get("title_seed") or ""),
        language=str(job_data.get("language") or "en"),
        target_minutes=float(job_data.get("target_minutes", 60.0)),
    )
    spec = JobSpec(
        job=job,
        category_key=str(raw.get("category_key") or "focus_lofi"),
        music=MusicConfig(**_section(raw, "music")),
        images=ImageConfig(**_section(raw, "images")),
        video=VideoConfig(**_section(raw, "video")),
        seo=SeoConfig(**_section(raw, "seo")),
        channel_style=ChannelStyleConfig(**_section(raw, "channel_style")),
        youtube=YoutubeConfig(**_section(raw, "youtube")),
        assets=AssetsConfig(**_section(raw, "assets")),
        source_path=path,
    )
    validate_spec(spec)
    return spec


def validate_spec(spec: JobSpec) -> None:
    if spec.job.target_minutes <= 0:
        raise ValueError("job.target_minutes must be greater than 0")
    if spec.music.track_count < 0:
        raise ValueError("music.track_count cannot be negative")
    if spec.music.track_duration_seconds <= 0 and spec.music.provider != "assets":
        raise ValueError("music.track_duration_seconds must be greater than 0")
    if spec.images.count < 0:
        raise ValueError("images.count cannot be negative")
    if spec.video.fps < 1 or spec.video.fps > 30:
        raise ValueError("video.fps must be between 1 and 30")
    if spec.video.visual_mode not in {"single", "slideshow"}:
        raise ValueError("video.visual_mode must be 'single' or 'slideshow'")
    if spec.youtube.privacy_status not in {"private", "unlisted", "public"}:
        raise ValueError("youtube.privacy_status must be private, unlisted, or public")
    _ = spec.video.width_height


def load_categories(path: Path | None = None) -> dict[str, dict[str, Any]]:
    categories_path = path or project_root() / "config" / "categories.yaml"
    data = yaml.safe_load(categories_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("categories.yaml must contain a mapping")
    return data


def category_for(spec: JobSpec, categories: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if spec.category_key not in categories:
        available = ", ".join(sorted(categories))
        raise KeyError(f"Unknown category_key '{spec.category_key}'. Available: {available}")
    return categories[spec.category_key]


def channel_style_prompt(style: ChannelStyleConfig, *, media: str) -> str:
    lines: list[str] = []
    if style.theme:
        lines.append(f"Channel theme: {style.theme.strip()}")
    if style.aesthetic:
        lines.append(f"Channel aesthetic: {style.aesthetic.strip()}")
    if media == "image":
        if style.visual_style:
            lines.append(f"Visual language: {style.visual_style.strip()}")
        if style.color_palette:
            lines.append(f"Color palette: {style.color_palette.strip()}")
    if media == "music" and style.sonic_identity:
        lines.append(f"Sonic identity: {style.sonic_identity.strip()}")
    if style.avoid:
        lines.append(f"Avoid: {style.avoid.strip()}")
    return "\n".join(line for line in lines if line)


def resolve_asset_paths(spec: JobSpec) -> tuple[list[Path], list[Path]]:
    base = spec.source_path.parent if spec.source_path else Path.cwd()
    audio = [Path(p) if Path(p).is_absolute() else base / p for p in spec.assets.audio_files]
    images = [Path(p) if Path(p).is_absolute() else base / p for p in spec.assets.image_files]
    return audio, images
