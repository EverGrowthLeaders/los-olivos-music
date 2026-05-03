from __future__ import annotations

import os
from pathlib import Path

from ...config import JobSpec
from ...prompt_safety import assert_prompt_is_licensing_safe
from ...utils import ensure_dir


class ElevenLabsMusicProvider:
    name = "elevenlabs"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is required for music.provider=elevenlabs")

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        ensure_dir(out_dir)
        try:
            from elevenlabs.client import ElevenLabs
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Install the elevenlabs package to use this provider") from exc

        client = ElevenLabs(api_key=self.api_key)
        prompt = spec.music.prompt or category.get("music_prompt")
        if not prompt:
            raise ValueError("A music prompt is required for ElevenLabs generation")
        assert_prompt_is_licensing_safe(prompt, field="music.prompt")
        if spec.music.instrumental and "no vocals" not in prompt.lower():
            prompt = f"{prompt}. No vocals, instrumental only."

        count = max(1, spec.music.track_count)
        length_ms = max(3000, spec.music.track_duration_seconds * 1000)
        paths: list[Path] = []
        for idx in range(count):
            out = out_dir / f"elevenlabs_track_{idx + 1:02d}.{spec.music.output_format}"
            if out.exists() and out.stat().st_size > 0:
                paths.append(out)
                continue
            track_prompt = f"{prompt}\nVariation {idx + 1}: keep the same mood, but create a new original melody and arrangement."
            audio_stream = client.music.compose(
                prompt=track_prompt,
                music_length_ms=length_ms,
            )
            with out.open("wb") as f:
                for chunk in audio_stream:
                    f.write(chunk)
            paths.append(out)
        return paths
