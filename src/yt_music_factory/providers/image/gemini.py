from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import requests

from ...config import JobSpec
from ...prompt_safety import assert_prompt_is_licensing_safe
from ...utils import ensure_dir


class GeminiImageProvider:
    """Nano Banana 2 / Gemini image generation via the Gemini REST API."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for images.provider=gemini")

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        ensure_dir(out_dir)
        prompt = spec.images.prompt or category.get("image_prompt")
        if not prompt:
            raise ValueError("An image prompt is required for Gemini image generation")
        assert_prompt_is_licensing_safe(prompt, field="images.prompt")
        count = max(1, spec.images.count)
        paths: list[Path] = []
        for idx in range(count):
            out = out_dir / f"gemini_background_{idx + 1:02d}.png"
            if out.exists() and out.stat().st_size > 0:
                paths.append(out)
                continue
            variation_prompt = (
                f"{prompt}\nVariation {idx + 1}: same art direction, different composition. "
                "Do not include text, typography, watermarks, logos, brand names, or UI."
            )
            payload = {
                "contents": [{"parts": [{"text": variation_prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": spec.images.aspect_ratio,
                        "imageSize": spec.images.image_size,
                    },
                },
            }
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            image_bytes = self._extract_image(response.json())
            if not image_bytes:
                raise RuntimeError(f"Gemini image response did not include image data: {response.text[:500]}")
            out.write_bytes(image_bytes)
            paths.append(out)
        return paths

    @staticmethod
    def _extract_image(payload: dict[str, Any]) -> bytes | None:
        for candidate in payload.get("candidates", []) or []:
            for part in candidate.get("content", {}).get("parts", []) or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    data = inline["data"]
                    if isinstance(data, str):
                        return base64.b64decode(data)
                    if isinstance(data, bytes):
                        return data
        return None
