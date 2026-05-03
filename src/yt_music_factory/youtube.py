from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any

from .seo import VideoMetadata

RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
RETRIABLE_EXCEPTIONS = ()
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def upload_video(
    video_path: Path,
    metadata: VideoMetadata,
    *,
    client_secrets: Path | None = None,
    token_file: Path | None = None,
    thumbnail_path: Path | None = None,
    chunksize: int = 8 * 1024 * 1024,
) -> str:
    """Upload a video to YouTube using resumable upload.

    The first run opens a browser for OAuth consent. Later runs reuse the stored token file.
    """
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    service = _get_youtube_service(client_secrets=client_secrets, token_file=token_file)
    try:
        from googleapiclient.http import MediaFileUpload
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("google-api-python-client is required for YouTube uploads") from exc

    body = metadata.to_upload_body()
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=chunksize, resumable=True)
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=metadata.notify_subscribers,
    )
    response = _resumable_upload(request)
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload response did not include a video id: {response}")
    if thumbnail_path and thumbnail_path.exists():
        thumb_media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg", resumable=False)
        service.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
    return str(video_id)


def _get_youtube_service(*, client_secrets: Path | None, token_file: Path | None):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Install Google API client libraries to upload to YouTube") from exc

    client_secrets = client_secrets or Path(os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secret.json"))
    token_file = token_file or Path(os.getenv("YOUTUBE_TOKEN_FILE", ".secrets/youtube-token.json"))
    if not client_secrets.exists():
        raise FileNotFoundError(
            f"YouTube client secrets not found: {client_secrets}. Create OAuth desktop credentials "
            "in Google Cloud and save them here."
        )

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), [YOUTUBE_UPLOAD_SCOPE])
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), [YOUTUBE_UPLOAD_SCOPE])
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def _resumable_upload(request) -> dict[str, Any]:
    response = None
    error = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if response is not None:
                return response
            if status:
                print(f"Uploaded {int(status.progress() * 100)}%")
        except Exception as exc:  # noqa: BLE001
            if _is_retriable(exc):
                error = exc
            else:
                raise
        if error is not None:
            retry += 1
            if retry > 10:
                raise RuntimeError(f"Upload failed after {retry} retries: {error}") from error
            sleep_seconds = min(60, (2**retry) + random.random())
            print(f"Retriable upload error: {error}. Sleeping {sleep_seconds:.1f}s before retry.")
            time.sleep(sleep_seconds)
            error = None
    raise RuntimeError("Upload finished without a response")


def _is_retriable(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in RETRIABLE_STATUS_CODES
