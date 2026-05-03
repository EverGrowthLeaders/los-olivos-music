from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import VideoConfig
from .utils import ensure_dir, require_binary, run_cmd, run_cmd_json


@dataclass(slots=True)
class AudioChapter:
    start_seconds: int
    title: str
    duration_seconds: int
    audio_file: Path


def ffprobe_duration(path: Path) -> float:
    require_binary("ffprobe")
    data = run_cmd_json(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        return float(data["format"]["duration"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Could not read duration for {path}: {data}") from exc


def _ffconcat_file_line(path: Path) -> str:
    # ffconcat uses single quotes and escapes embedded single quotes as '\''.
    escaped = str(path.resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


def write_audio_manifest(audio_files: list[Path], target_seconds: int, manifest_path: Path) -> Path:
    if not audio_files:
        raise ValueError("At least one audio file is required")
    ensure_dir(manifest_path.parent)
    durations = [max(0.1, ffprobe_duration(path)) for path in audio_files]
    lines = ["ffconcat version 1.0"]
    elapsed = 0.0
    idx = 0
    # Repeat the track list until the target duration is covered.
    while elapsed < target_seconds + 2:
        path = audio_files[idx % len(audio_files)]
        lines.append(_ffconcat_file_line(path))
        elapsed += durations[idx % len(audio_files)]
        idx += 1
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def build_audio_chapters(audio_files: list[Path], target_seconds: int) -> list[AudioChapter]:
    if not audio_files:
        return []
    durations = [max(1, int(round(ffprobe_duration(path)))) for path in audio_files]
    chapters: list[AudioChapter] = []
    elapsed = 0
    idx = 0
    while elapsed < target_seconds:
        audio_idx = idx % len(audio_files)
        duration = min(durations[audio_idx], max(1, target_seconds - elapsed))
        loop = idx // len(audio_files) + 1
        title = f"Track {audio_idx + 1:02d}"
        if loop > 1:
            title = f"{title} (loop {loop})"
        chapters.append(
            AudioChapter(
                start_seconds=elapsed,
                title=title,
                duration_seconds=duration,
                audio_file=audio_files[audio_idx],
            )
        )
        elapsed += duration
        idx += 1
    return chapters


def transcode_audio_inputs(audio_files: list[Path], out_dir: Path) -> list[Path]:
    """Normalize stream parameters before concat.

    This avoids concat-demuxer failures when providers return MP3/WAV files with different sample
    rates, channel layouts, or codecs. It costs one light pass over each short track but makes the
    one-hour render reliable.
    """
    require_binary("ffmpeg")
    ensure_dir(out_dir)
    normalized: list[Path] = []
    for idx, src in enumerate(audio_files, start=1):
        if not src.exists():
            raise FileNotFoundError(f"Audio file does not exist: {src}")
        dst = out_dir / f"audio_norm_{idx:03d}.wav"
        if dst.exists() and dst.stat().st_size > 0:
            normalized.append(dst)
            continue
        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                str(dst),
            ],
            quiet=True,
        )
        normalized.append(dst)
    return normalized


def make_audio_loop(
    audio_files: list[Path],
    out_audio: Path,
    target_seconds: int,
    work_dir: Path,
    *,
    audio_bitrate: str = "192k",
    normalize_audio: bool = False,
) -> Path:
    require_binary("ffmpeg")
    ensure_dir(out_audio.parent)
    normalized = transcode_audio_inputs(audio_files, work_dir / "normalized_audio")
    manifest = write_audio_manifest(normalized, target_seconds, work_dir / "audio_concat.ffconcat")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
        "-t",
        str(target_seconds),
        "-vn",
    ]
    if normalize_audio:
        cmd += ["-af", "loudnorm=I=-14:LRA=11:TP=-1.5"]
    cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", "44100", str(out_audio)]
    run_cmd(cmd)
    return out_audio


def write_image_manifest(
    image_files: list[Path],
    target_seconds: int,
    image_duration_seconds: int,
    manifest_path: Path,
) -> Path:
    if not image_files:
        raise ValueError("At least one image file is required")
    ensure_dir(manifest_path.parent)
    duration = max(1, image_duration_seconds)
    lines = ["ffconcat version 1.0"]
    elapsed = 0
    idx = 0
    last = image_files[0]
    while elapsed < target_seconds:
        last = image_files[idx % len(image_files)]
        lines.append(_ffconcat_file_line(last))
        lines.append(f"duration {min(duration, max(1, target_seconds - elapsed))}")
        elapsed += duration
        idx += 1
    # concat demuxer needs the last file repeated to apply the previous duration.
    lines.append(_ffconcat_file_line(last))
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def make_video(
    image_files: list[Path],
    audio_file: Path,
    out_video: Path,
    target_seconds: int,
    video: VideoConfig,
    work_dir: Path,
) -> Path:
    require_binary("ffmpeg")
    ensure_dir(out_video.parent)
    if not image_files:
        raise ValueError("At least one image is required")
    for image in image_files:
        if not image.exists():
            raise FileNotFoundError(f"Image file does not exist: {image}")
    width, height = video.width_height
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps={video.fps}"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if video.visual_mode == "single" or len(image_files) == 1:
        cmd += ["-loop", "1", "-framerate", str(video.fps), "-i", str(image_files[0])]
    else:
        manifest = write_image_manifest(
            image_files,
            target_seconds,
            video.image_duration_seconds,
            work_dir / "image_concat.ffconcat",
        )
        cmd += ["-f", "concat", "-safe", "0", "-i", str(manifest)]
    cmd += [
        "-i",
        str(audio_file),
        "-t",
        str(target_seconds),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        video.video_preset,
        "-tune",
        "stillimage",
        "-g",
        str(max(1, video.fps * 10)),
        "-c:a",
        "copy",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_video),
    ]
    run_cmd(cmd)
    return out_video


def make_thumbnail(image_file: Path, title: str, out_path: Path, resolution: tuple[int, int] = (1280, 720)) -> Path:
    ensure_dir(out_path.parent)
    width, height = resolution
    image = Image.open(image_file).convert("RGB")
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (8, 8, 12))
    x = (width - image.width) // 2
    y = (height - image.height) // 2
    canvas.paste(image, (x, y))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, height - 230, width, height], fill=(0, 0, 0, 125))
    font = _font(max(42, width // 24))
    small = _font(max(24, width // 48))
    lines = _wrap_title(title, max_chars=28, max_lines=2)
    text_y = height - 195
    for line in lines:
        draw.text((55, text_y), line, font=font, fill=(255, 255, 255, 240))
        text_y += int(font.size * 1.15) if hasattr(font, "size") else 52
    draw.text((58, height - 55), "Original music mix", font=small, fill=(230, 230, 235, 220))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    canvas = canvas.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=3))
    canvas.save(out_path, quality=92, optimize=True)
    return out_path


def _wrap_title(title: str, *, max_chars: int, max_lines: int) -> list[str]:
    words = title.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if len(candidate) <= max_chars or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    if not lines:
        return [title[:max_chars]]
    return lines[:max_lines]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()
