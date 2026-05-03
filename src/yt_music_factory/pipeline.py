from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .asset_registry import AssetRegistry, build_track_metadata
from .config import JobSpec, category_for, load_categories, load_spec, resolve_asset_paths
from .ffmpeg import extend_clip_with_crossfade, make_audio_from_timeline, make_thumbnail, make_video
from .providers.image import get_image_provider
from .providers.music import get_music_provider
from .storage import store_clip_asset
from .seo import VideoChapter, VideoMetadata, build_metadata, save_metadata
from .strategy_store import load_effective_strategy
from .timeline.planner import estimate_new_track_count, plan_timeline
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
            "chapters": [
                {"start_seconds": chapter.start_seconds, "title": chapter.title}
                for chapter in self.metadata.chapters or []
            ],
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
    channel_id = os.getenv("YMF_CHANNEL_ID", "default")
    policy = load_effective_strategy(channel_id=channel_id, category_key=spec.category_key, override=spec.asset_strategy)
    channel_theme = spec.channel_style.theme or category.get("label") or spec.category_key
    registry = AssetRegistry(workdir / "data" / "assets.sqlite")
    history = registry.get_video_history(channel_id, str(channel_theme))
    other_history = registry.get_other_video_history(channel_id, str(channel_theme))
    reusable_tracks = registry.find_reusable_tracks(
        str(channel_theme),
        spec.category_key,
        policy,
        {"channel_id": channel_id, "history": history},
    )

    asset_audio, asset_images = resolve_asset_paths(spec)
    audio_files = [p for p in asset_audio if p.exists()]
    missing_audio = [str(p) for p in asset_audio if not p.exists()]
    if missing_audio:
        raise FileNotFoundError(f"Missing audio asset(s): {missing_audio}")
    if not audio_files:
        provider = get_music_provider(spec.music.provider)
        if provider is None:
            raise ValueError("No audio assets found and music.provider is assets/none")
        requested_track_count = max(
            1,
            estimate_new_track_count(
                target_seconds=spec.job.target_seconds,
                source_track_seconds=max(1, int(spec.music.track_duration_seconds)),
                available_reusable_count=len(reusable_tracks),
                policy=policy,
            ),
        )
        spec.music.track_count = requested_track_count
        print(
            f"[audio] Generating {max(1, spec.music.track_count)} track(s) "
            f"with provider '{spec.music.provider}'",
            flush=True,
        )
        audio_files = provider.generate(spec, category, audio_dir)
    print(f"[audio] Ready: {len(audio_files)} source audio file(s)", flush=True)

    new_tracks = []
    stored_dir = ensure_dir(workdir / "data" / "stored_clips")
    for audio_file in audio_files:
        metadata = build_track_metadata(
            source_file=audio_file,
            provider=spec.music.provider if not asset_audio else "assets",
            model=spec.music.model or os.getenv("GEMINI_MUSIC_MODEL", ""),
            channel_id=channel_id,
            channel_theme=str(channel_theme),
            substyle=spec.category_key,
            prompt=str(spec.music.prompt or category.get("music_prompt") or ""),
        )
        try:
            stored = store_clip_asset(audio_file, track_id=metadata["id"], out_dir=stored_dir)
            metadata["stored_asset_file"] = str(stored.local_path)
            metadata["r2_key"] = stored.r2_key
            metadata["r2_url"] = stored.r2_url
        except Exception as exc:  # noqa: BLE001
            print(f"[assets] Storage skipped for {audio_file}: {exc}", flush=True)
        new_tracks.append(registry.register_track(metadata))

    print("[timeline] Planning reuse/extension timeline", flush=True)
    max_retries = int(policy.get("on_gate_fail", {}).get("max_retries") or 0)
    timeline_manifest = None
    for attempt in range(max_retries + 1):
        timeline_manifest = plan_timeline(
            job_id=spec.job.slug,
            channel_id=channel_id,
            theme=str(channel_theme),
            target_seconds=spec.job.target_seconds,
            new_tracks=new_tracks,
            reusable_tracks=reusable_tracks,
            policy=policy,
            history=history,
            other_channel_history=other_history,
            shuffle_seed=f"{spec.job.slug}:{attempt}",
        )
        if timeline_manifest.get("gate_passed", False):
            if attempt:
                print(f"[timeline] Gate passed after retry {attempt}/{max_retries}", flush=True)
            break
        if attempt < max_retries:
            actions = ", ".join(policy.get("on_gate_fail", {}).get("actions") or ["reshuffle"])
            print(f"[timeline] Gate failed on attempt {attempt + 1}; retrying with {actions}", flush=True)
    assert timeline_manifest is not None
    manifest_path = job_dir / "video_manifest.json"
    write_json(manifest_path, timeline_manifest)
    if not timeline_manifest.get("gate_passed", False):
        raise RuntimeError(f"Publish gate failed before render: {timeline_manifest.get('gate_failures')}")
    print(f"[timeline] Manifest ready: {manifest_path}", flush=True)

    print("[metadata] Building automatic chapters from timeline", flush=True)
    chapters = [
        VideoChapter(int(item["start_seconds"]), f"Track {idx:02d}")
        for idx, item in enumerate(timeline_manifest["timeline"], start=1)
    ]
    print(f"[metadata] Ready: {len(chapters)} chapter marker(s)", flush=True)
    metadata = build_metadata(spec, category, chapters=chapters)
    metadata_path = save_metadata(job_dir / "youtube_metadata.json", metadata)
    print(f"[metadata] Metadata ready: {metadata_path}", flush=True)

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

    print("[render] Building final audio timeline", flush=True)
    extension_dir = ensure_dir(render_dir / "extended_clips")
    for idx, item in enumerate(timeline_manifest["timeline"], start=1):
        if item.get("is_extended"):
            extended = extend_clip_with_crossfade(
                Path(item["source_file"]),
                extension_dir / f"extended_{idx:03d}.m4a",
                int(item["planned_duration_seconds"]),
                int(item.get("crossfade_seconds") or 0),
                bool(policy.get("internal_extension", {}).get("require_micro_variation", True)),
            )
            item["render_source_file"] = str(extended.path)
            item["extension_filters"] = extended.filters_used
            item["extended_duration_actual_seconds"] = extended.duration_seconds
        else:
            item["render_source_file"] = item["source_file"]
    write_json(manifest_path, timeline_manifest)
    final_audio = make_audio_from_timeline(
        timeline_manifest["timeline"],
        render_dir / f"{spec.job.slug}.m4a",
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

    result = PipelineResult(
        job_dir=job_dir,
        audio_files=audio_files,
        image_files=image_files,
        final_audio=final_audio,
        final_video=final_video,
        thumbnail=thumbnail,
        metadata_path=metadata_path,
        metadata=metadata,
    )
    result_path = job_dir / "result.json"
    write_json(result_path, result.to_json())
    registry.update_usage_after_render(timeline_manifest)
    print(f"[pipeline] Render result saved: {result_path}", flush=True)

    youtube_video_id = None
    should_upload = spec.youtube.upload if upload is None else upload
    if should_upload:
        print("[youtube] Upload started", flush=True)
        youtube_video_id = upload_video(
            final_video,
            metadata,
            thumbnail_path=thumbnail if spec.youtube.set_thumbnail else None,
        )
        result.youtube_video_id = youtube_video_id
        write_json(result_path, result.to_json())
        print(f"[youtube] Upload complete: {youtube_video_id}", flush=True)
    else:
        print("[youtube] Upload skipped", flush=True)

    print(f"[pipeline] Job complete: {result_path}", flush=True)
    return result
