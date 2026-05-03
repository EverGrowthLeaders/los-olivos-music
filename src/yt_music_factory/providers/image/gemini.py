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
        self.model = self._normalize_model(model or os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview"))
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
            payload = self._build_payload(variation_prompt, spec.images.aspect_ratio, spec.images.image_size)
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=300,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Gemini image request failed with HTTP {response.status_code} "
                    f"for model {self.model}: {response.text[:1000]}"
                )
            image_bytes = self._extract_image(response.json())
            if not image_bytes:
                raise RuntimeError(f"Gemini image response did not include image data: {response.text[:500]}")
            out.write_bytes(image_bytes)
            paths.append(out)
        return paths

    @staticmethod
    def _normalize_model(value: str) -> str:
        model = (value or "gemini-3-pro-image-preview").strip()
        legacy_models = {
            "gemini-3.1-flash-image-preview",
            "gemini-3.1-flash-image",
            "gemini-3-flash-image-preview",
        }
        if model in legacy_models:
            return "gemini-3-pro-image-preview"
        return model

    @staticmethod
    def _build_payload(prompt: str, aspect_ratio: str, image_size: str) -> dict[str, Any]:
        image_config: dict[str, str] = {"aspectRatio": aspect_ratio}
        if image_size:
            image_config["imageSize"] = image_size
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": image_config,
            },
        }

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
