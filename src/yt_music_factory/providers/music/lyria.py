from __future__ import annotations

import asyncio
import inspect
import os
import wave
from pathlib import Path
from typing import Any

from ...config import JobSpec
from ...prompt_safety import assert_prompt_is_licensing_safe
from ...utils import ensure_dir, write_json


PCM_SAMPLE_RATE = 48_000
PCM_CHANNELS = 2
PCM_SAMPLE_WIDTH = 2


class LyriaMusicProvider:
    """Google Lyria RealTime music generation through the Gemini Live Music API.

    Lyria music generation is not a normal generateContent request. The public Gemini
    API exposes it as a bidirectional Live Music/WebSocket stream that emits raw
    16-bit PCM stereo audio at 48 kHz.
    """

    name = "lyria"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = self._normalize_model(model or os.getenv("GEMINI_MUSIC_MODEL", "lyria-realtime-exp"))
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for music.provider=lyria")

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        ensure_dir(out_dir)
        prompt = spec.music.prompt or category.get("music_prompt")
        if not prompt:
            raise ValueError("A music prompt is required for Lyria generation")
        assert_prompt_is_licensing_safe(prompt, field="music.prompt")

        model = self._normalize_model(spec.music.model or self.model)
        count = max(1, spec.music.track_count)
        paths: list[Path] = []

        for idx in range(count):
            out = out_dir / f"lyria_track_{idx + 1:02d}.wav"
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
            audio_bytes = asyncio.run(
                self._generate_pcm_stream(
                    model=model,
                    prompt=track_prompt,
                    duration_seconds=spec.music.track_duration_seconds,
                )
            )
            if not audio_bytes:
                raise RuntimeError(
                    "Lyria stream ended before audio was received. "
                    "Check model access, safety filters, and Gemini API billing/quota."
                )

            self._write_wav(out, audio_bytes)
            write_json(
                sidecar,
                {
                    "provider": self.name,
                    "model": model,
                    "track_number": idx + 1,
                    "prompt": track_prompt,
                    "format": "pcm16-wav",
                    "sample_rate_hz": PCM_SAMPLE_RATE,
                    "channels": PCM_CHANNELS,
                },
            )
            paths.append(out)

        return paths

    @staticmethod
    def _normalize_model(value: str) -> str:
        model = (value or "lyria-realtime-exp").strip()
        legacy_rest_models = {"lyria-3-pro-preview", "lyria-3-pro", "models/lyria-3-pro-preview"}
        if model in legacy_rest_models:
            return "models/lyria-realtime-exp"
        if not model.startswith("models/"):
            return f"models/{model}"
        return model

    @staticmethod
    def _build_track_prompt(
        *,
        base_prompt: str,
        track_number: int,
        duration_seconds: int,
        instrumental: bool,
    ) -> str:
        duration = max(10, int(duration_seconds))
        vocal_clause = (
            "Instrumental only, no vocals, no lyrics."
            if instrumental
            else "Original non-lyrical vocalizations are allowed, but do not imitate any real singer."
        )
        return (
            f"Generate approximately {duration} seconds of original music. {vocal_clause}\n"
            f"Creative direction: {base_prompt}\n"
            f"Variation {track_number}: use a distinct original melody, groove, and arrangement "
            "while keeping the same mood and use case. Do not imitate any existing artist, song, "
            "label, performer, or copyrighted recording."
        )

    async def _generate_pcm_stream(self, *, model: str, prompt: str, duration_seconds: int) -> bytes:
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Install google-genai to use Lyria music generation") from exc

        target_bytes = max(1, int(duration_seconds)) * PCM_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH
        chunks: list[bytes] = []
        client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})

        async with client.aio.live.music.connect(model=model) as session:
            await session.set_weighted_prompts(prompts=[types.WeightedPrompt(text=prompt, weight=1.0)])
            await session.play()

            try:
                while sum(len(chunk) for chunk in chunks) < target_bytes:
                    async for message in session.receive():
                        for chunk in self._audio_chunks_from_message(message):
                            chunks.append(chunk)
                            if sum(len(item) for item in chunks) >= target_bytes:
                                break
                        if sum(len(item) for item in chunks) >= target_bytes:
                            break
            finally:
                for method_name in ("stop", "pause"):
                    method = getattr(session, method_name, None)
                    if method is None:
                        continue
                    result = method()
                    if inspect.isawaitable(result):
                        await result
                    break

        return b"".join(chunks)[:target_bytes]

    @staticmethod
    def _audio_chunks_from_message(message: Any) -> list[bytes]:
        server_content = getattr(message, "server_content", None) or getattr(message, "serverContent", None)
        audio_chunks = getattr(server_content, "audio_chunks", None) or getattr(server_content, "audioChunks", None)
        result: list[bytes] = []
        for chunk in audio_chunks or []:
            data = getattr(chunk, "data", None)
            if isinstance(chunk, dict):
                data = chunk.get("data")
            if isinstance(data, str):
                import base64

                result.append(base64.b64decode(data))
            elif isinstance(data, bytes):
                result.append(data)
        return result

    @staticmethod
    def _write_wav(path: Path, pcm: bytes) -> None:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(PCM_CHANNELS)
            wav.setsampwidth(PCM_SAMPLE_WIDTH)
            wav.setframerate(PCM_SAMPLE_RATE)
            wav.writeframes(pcm)
