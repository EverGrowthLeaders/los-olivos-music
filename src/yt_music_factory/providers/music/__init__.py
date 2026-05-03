from __future__ import annotations

from .local import LocalMusicProvider
from .lyria import LyriaMusicProvider


def get_music_provider(name: str):
    normalized = name.strip().lower()
    if normalized in {"assets", "none"}:
        return None
    if normalized == "local":
        return LocalMusicProvider()
    if normalized in {"lyria", "lyria3", "lyria-3", "lyria-3-pro"}:
        return LyriaMusicProvider()
    raise ValueError(f"Unknown music provider: {name}")
