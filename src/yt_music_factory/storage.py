from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import ffprobe_duration
from .utils import ensure_dir, run_cmd


@dataclass(slots=True)
class StoredAsset:
    local_path: Path
    duration_seconds: float
    r2_key: str | None = None
    r2_url: str | None = None


def prepare_clip_distribution_copy(input_path: Path, out_dir: Path, *, codec: str = "aac") -> Path:
    ensure_dir(out_dir)
    suffix = ".m4a" if codec == "aac" else ".mp3"
    output = out_dir / f"{input_path.stem}_256k{suffix}"
    if output.exists() and output.stat().st_size > 0:
        return output
    if codec == "mp3":
        codec_args = ["-c:a", "libmp3lame"]
    else:
        codec_args = ["-c:a", "aac"]
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vn",
            *codec_args,
            "-b:a",
            "256k",
            "-ar",
            "44100",
            str(output),
        ],
        quiet=True,
    )
    return output


def store_clip_asset(input_path: Path, *, track_id: str, out_dir: Path) -> StoredAsset:
    codec = os.getenv("YMF_ASSET_AUDIO_FORMAT", "aac").strip().lower()
    if codec not in {"aac", "mp3"}:
        codec = "aac"
    local_copy = prepare_clip_distribution_copy(input_path, out_dir, codec=codec)
    r2_key = f"clips/{track_id}/{local_copy.name}"
    r2_url = upload_to_r2_if_configured(local_copy, r2_key)
    return StoredAsset(local_path=local_copy, duration_seconds=ffprobe_duration(local_copy), r2_key=r2_key if r2_url else None, r2_url=r2_url)


def upload_to_r2_if_configured(local_path: Path, key: str) -> str | None:
    bucket = os.getenv("R2_BUCKET")
    endpoint = os.getenv("R2_ENDPOINT_URL")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    if not all([bucket, endpoint, access_key, secret_key]):
        return None
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("boto3 is required when R2 storage is configured") from exc
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=os.getenv("R2_REGION", "auto"),
    )
    content_type = "audio/mp4" if local_path.suffix.lower() == ".m4a" else "audio/mpeg"
    client.upload_file(str(local_path), bucket, key, ExtraArgs={"ContentType": content_type})
    public_base = os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/")
    return f"{public_base}/{key}" if public_base else f"r2://{bucket}/{key}"

