from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

from ...config import JobSpec, channel_style_prompt
from ...prompt_safety import assert_prompt_is_licensing_safe
from ...utils import download_file, ensure_dir


class MubertMusicProvider:
    name = "mubert"
    base_url = "https://music-api.mubert.com/api/v3/public"

    def __init__(self, customer_id: str | None = None, access_token: str | None = None) -> None:
        self.customer_id = customer_id or os.getenv("MUBERT_CUSTOMER_ID")
        self.access_token = access_token or os.getenv("MUBERT_ACCESS_TOKEN")
        if not self.customer_id or not self.access_token:
            raise RuntimeError("MUBERT_CUSTOMER_ID and MUBERT_ACCESS_TOKEN are required")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "customer-id": self.customer_id or "",
            "access-token": self.access_token or "",
            "Content-Type": "application/json",
        }

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        ensure_dir(out_dir)
        count = max(1, spec.music.track_count)
        paths: list[Path] = []
        for idx in range(count):
            suffix = spec.music.output_format if spec.music.output_format in {"mp3", "wav"} else "mp3"
            out = out_dir / f"mubert_track_{idx + 1:02d}.{suffix}"
            if out.exists() and out.stat().st_size > 0:
                paths.append(out)
                continue
            track = self._create_track(spec, category, variation=idx + 1)
            track_id = track.get("id")
            if not track_id:
                raise RuntimeError(f"Mubert did not return a track id: {track}")
            completed = self._poll_track(track_id)
            url = self._extract_url(completed)
            if not url:
                raise RuntimeError(f"Mubert track finished without a download URL: {completed}")
            download_file(url, out)
            paths.append(out)
        return paths

    def _create_track(self, spec: JobSpec, category: dict, variation: int) -> dict[str, Any]:
        prompt = spec.music.prompt or category.get("music_prompt")
        style_note = channel_style_prompt(spec.channel_style, media="music")
        if prompt and style_note:
            prompt = f"{prompt}\n\nAccount-level creative direction:\n{style_note}"
        payload: dict[str, Any] = {
            "duration": spec.music.track_duration_seconds,
            "bitrate": spec.music.bitrate,
            "format": spec.music.output_format if spec.music.output_format in {"mp3", "wav"} else "mp3",
            "intensity": spec.music.intensity,
            "mode": spec.music.mode,
        }
        if spec.music.playlist_index:
            payload["playlist_index"] = spec.music.playlist_index
        elif prompt:
            assert_prompt_is_licensing_safe(prompt, field="music.prompt")
            payload["prompt"] = f"{prompt[:180]} variation {variation}"[:200]
        else:
            raise ValueError("Mubert requires either music.playlist_index or a prompt/category prompt")

        response = requests.post(f"{self.base_url}/tracks", headers=self.headers, json=payload, timeout=120)
        response.raise_for_status()
        if response.status_code == 204:
            raise RuntimeError(
                "Mubert returned 204 No Content. Configure a playlist_index or webhook-enabled plan, "
                "or contact Mubert to enable track generation response bodies."
            )
        data = response.json().get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Mubert response: {response.text[:500]}")
        return data

    def _poll_track(self, track_id: str, *, timeout_seconds: int = 900) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        sleep = 5.0
        while time.time() < deadline:
            response = requests.get(f"{self.base_url}/tracks/{track_id}", headers=self.headers, timeout=60)
            response.raise_for_status()
            data = response.json().get("data")
            if isinstance(data, dict):
                statuses = [g.get("status") for g in data.get("generations", []) if isinstance(g, dict)]
                if "done" in statuses and self._extract_url(data):
                    return data
                if any(status in {"failed", "error"} for status in statuses):
                    raise RuntimeError(f"Mubert generation failed: {data}")
            time.sleep(sleep)
            sleep = min(20.0, sleep * 1.3)
        raise TimeoutError(f"Timed out waiting for Mubert track {track_id}")

    @staticmethod
    def _extract_url(track: dict[str, Any]) -> str | None:
        for generation in track.get("generations", []) or []:
            if isinstance(generation, dict) and generation.get("status") == "done" and generation.get("url"):
                return str(generation["url"])
        return None
