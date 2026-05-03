from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import JobSpec, category_for, load_categories, load_spec, resolve_asset_paths
from .ffmpeg import make_audio_loop, make_thumbnail, make_video
from .providers.image import get_image_provider
from .providers.music import get_music_provider
from .seo import VideoMetadata, build_metadata, save_metadata
from .utils import ensure_dir, write_json
from .youtube import upload_video


@dataclass(slots=True)
class PipelineResult:
    job_dir: Path
    audio_files: list[Path]
    image_files: list[Path]
    final_audio: Path
    final_video: Path
    thumbnail: Path
    metadata_path: Path
    metadata: VideoMetadata
    youtube_video_id: str | None = None

    def to_json(self) -> dict:
        return {
            "job_dir": str(self.job_dir),
            "audio_files": [str(p) for p in self.audio_files],
            "image_files": [str(p) for p in self.image_files],
            "final_audio": str(self.final_audio),
            "final_video": str(self.final_video),
            "thumbnail": str(self.thumbnail),
            "metadata_path": str(self.metadata_path),
            "youtube_video_id": self.youtube_video_id,
        }


def run_pipeline(
    spec_path: Path,
    *,
    workdir: Path,
    upload: bool | None = None,
    categories_path: Path | None = None,
) -> PipelineResult:
    spec = load_spec(spec_path)
    categories = load_categories(categories_path)
    category = category_for(spec, categories)
    return run_loaded_pipeline(spec, category, workdir=workdir, upload=upload)


def run_loaded_pipeline(
    spec: JobSpec,
    category: dict,
    *,
    workdir: Path,
    upload: bool | None = None,
) -> PipelineResult:
    job_dir = ensure_dir(workdir / spec.job.slug)
    audio_dir = ensure_dir(job_dir / "audio")
    image_dir = ensure_dir(job_dir / "images")
    render_dir = ensure_dir(job_dir / "render")
    print(f"[pipeline] Job '{spec.job.slug}' started", flush=True)
    print(f"[pipeline] Target duration: {spec.job.target_minutes:g} minutes", flush=True)
    metadata = build_metadata(spec, category)
    metadata_path = save_metadata(job_dir / "youtube_metadata.json", metadata)
    print(f"[pipeline] Metadata ready: {metadata_path}", flush=True)

    asset_audio, asset_images = resolve_asset_paths(spec)
    audio_files = [p for p in asset_audio if p.exists()]
    missing_audio = [str(p) for p in asset_audio if not p.exists()]
    if missing_audio:
        raise FileNotFoundError(f"Missing audio asset(s): {missing_audio}")
    if not audio_files:
        provider = get_music_provider(spec.music.provider)
        if provider is None:
            raise ValueError("No audio assets found and music.provider is assets/none")
        print(
            f"[audio] Generating {max(1, spec.music.track_count)} track(s) "
            f"with provider '{spec.music.provider}'",
            flush=True,
        )
        audio_files = provider.generate(spec, category, audio_dir)
    print(f"[audio] Ready: {len(audio_files)} source audio file(s)", flush=True)

    image_files = [p for p in asset_images if p.exists()]
    missing_images = [str(p) for p in asset_images if not p.exists()]
    if missing_images:
        raise FileNotFoundError(f"Missing image asset(s): {missing_images}")
    if not image_files:
        provider = get_image_provider(spec.images.provider)
        if provider is None:
            raise ValueError("No image assets found and images.provider is assets/none")
        print(
            f"[images] Generating {spec.images.count} image(s) with provider '{spec.images.provider}'",
            flush=True,
        )
        image_files = provider.generate(spec, category, image_dir)
    print(f"[images] Ready: {len(image_files)} image file(s)", flush=True)

    print("[render] Building final audio loop", flush=True)
    final_audio = make_audio_loop(
        audio_files,
        render_dir / f"{spec.job.slug}.m4a",
        spec.job.target_seconds,
        render_dir,
        audio_bitrate=spec.video.audio_bitrate,
        normalize_audio=spec.video.normalize_audio,
    )
    print(f"[render] Final audio ready: {final_audio}", flush=True)
    print("[render] Building final video", flush=True)
    final_video = make_video(
        image_files,
        final_audio,
        render_dir / f"{spec.job.slug}.mp4",
        spec.job.target_seconds,
        spec.video,
        render_dir,
    )
    print(f"[render] Final video ready: {final_video}", flush=True)
    print("[render] Building thumbnail", flush=True)
    thumbnail = make_thumbnail(image_files[0], metadata.title, render_dir / f"{spec.job.slug}_thumb.jpg")
    print(f"[render] Thumbnail ready: {thumbnail}", flush=True)

    youtube_video_id = None
    should_upload = spec.youtube.upload if upload is None else upload
    if should_upload:
        print("[youtube] Upload started", flush=True)
        youtube_video_id = upload_video(
            final_video,
            metadata,
            thumbnail_path=thumbnail if spec.youtube.set_thumbnail else None,
        )
        print(f"[youtube] Upload complete: {youtube_video_id}", flush=True)
    else:
        print("[youtube] Upload skipped", flush=True)

    result = PipelineResult(
        job_dir=job_dir,
        audio_files=audio_files,
        image_files=image_files,
        final_audio=final_audio,
        final_video=final_video,
        thumbnail=thumbnail,
        metadata_path=metadata_path,
        metadata=metadata,
        youtube_video_id=youtube_video_id,
    )
    write_json(job_dir / "result.json", result.to_json())
    print(f"[pipeline] Job complete: {job_dir / 'result.json'}", flush=True)
    return result
