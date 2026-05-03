from __future__ import annotations

from .gemini import GeminiImageProvider
from .local import LocalImageProvider


def get_image_provider(name: str):
    normalized = name.strip().lower()
    if normalized in {"assets", "none"}:
        return None
    if normalized == "local":
        return LocalImageProvider()
    if normalized in {"gemini", "nanobanana", "nano_banana", "nano-banana-2"}:
        return GeminiImageProvider()
    raise ValueError(f"Unknown image provider: {name}")
