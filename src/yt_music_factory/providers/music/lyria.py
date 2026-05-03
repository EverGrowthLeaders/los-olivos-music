from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import requests

from ...config import JobSpec
from ...prompt_safety import assert_prompt_is_licensing_safe
from ...utils import ensure_dir, write_json


class LyriaMusicProvider:
    """Google Lyria 3 music generation through the Gemini REST API.

    Uses the public Gemini API model endpoint by default:
    https://generativelanguage.googleapis.com/v1beta/models/lyria-3-pro-preview:generateContent
    """

    name = "lyria"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MUSIC_MODEL", "lyria-3-pro-preview")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for music.provider=lyria")

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        ensure_dir(out_dir)
        prompt = spec.music.prompt or category.get("music_prompt")
        if not prompt:
            raise ValueError("A music prompt is required for Lyria generation")
        assert_prompt_is_licensing_safe(prompt, field="music.prompt")

        model = spec.music.model or self.model
        output_format = self._normalize_output_format(spec.music.output_format)
        count = max(1, spec.music.track_count)
        paths: list[Path] = []

        for idx in range(count):
            out = out_dir / f"lyria_track_{idx + 1:02d}.{output_format}"
            sidecar = out.with_suffix(".json")
            if out.exists() and out.stat().st_size > 0:
                paths.append(out)
                continue

            track_prompt = self._build_track_prompt(
                base_prompt=prompt,
                track_number=idx + 1,
                duration_seconds=spec.music.track_duration_seconds,
                instrumental=spec.music.instrumental,
            )
            payload = self._build_payload(track_prompt)
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=600,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Lyria request failed with HTTP {response.status_code}: {response.text[:1000]}"
                )

            parsed = response.json()
            audio_bytes, text_parts = self._extract_audio_and_text(parsed)
            write_json(
                sidecar,
                {
                    "provider": self.name,
                    "model": model,
                    "track_number": idx + 1,
                    "prompt": track_prompt,
                    "text_parts": text_parts,
                },
            )
            if not audio_bytes:
                write_json(out_dir / f"lyria_track_{idx + 1:02d}_raw_response.json", parsed)
                raise RuntimeError("Lyria response did not include audio data")

            out.write_bytes(audio_bytes)
            paths.append(out)

        return paths

    @staticmethod
    def _normalize_output_format(value: str) -> str:
        normalized = (value or "mp3").lower().strip().lstrip(".")
        if normalized in {"mp3", "mpeg"}:
            return "mp3"
        if normalized == "wav":
            return "wav"
        raise ValueError("Lyria music.output_format must be 'mp3' or 'wav'")

    @staticmethod
    def _build_track_prompt(
        *,
        base_prompt: str,
        track_number: int,
        duration_seconds: int,
        instrumental: bool,
    ) -> str:
        duration = max(30, int(duration_seconds))
        vocal_clause = "Instrumental only, no vocals, no lyrics." if instrumental else "Original vocals and lyrics are allowed."
        return (
            f"Create an original {duration}-second music track. {vocal_clause}\n"
            f"Creative direction: {base_prompt}\n"
            f"Variation {track_number}: use a new original melody, harmonic progression, and arrangement "
            "while keeping the same mood and use case. Do not imitate any existing artist, song, "
            "label, performer, or copyrighted recording."
        )

    @staticmethod
    def _build_payload(prompt: str) -> dict[str, Any]:
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO", "TEXT"]},
        }

    @staticmethod
    def _extract_audio_and_text(payload: dict[str, Any]) -> tuple[bytes | None, list[str]]:
        text_parts: list[str] = []
        audio_bytes: bytes | None = None
        for candidate in payload.get("candidates", []) or []:
            content = candidate.get("content") or {}
            for part in content.get("parts", []) or []:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text)
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    data = inline["data"]
                    if isinstance(data, str):
                        audio_bytes = base64.b64decode(data)
                    elif isinstance(data, bytes):
                        audio_bytes = data
        return audio_bytes, text_parts
