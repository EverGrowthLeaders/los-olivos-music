from __future__ import annotations

from .elevenlabs import ElevenLabsMusicProvider
from .local import LocalMusicProvider
from .lyria import LyriaMusicProvider
from .mubert import MubertMusicProvider


def get_music_provider(name: str):
    normalized = name.strip().lower()
    if normalized in {"assets", "none"}:
        return None
    if normalized == "local":
        return LocalMusicProvider()
    if normalized == "elevenlabs":
        return ElevenLabsMusicProvider()
    if normalized in {"lyria", "lyria3", "lyria-3", "lyria-3-pro"}:
        return LyriaMusicProvider()
    if normalized == "mubert":
        return MubertMusicProvider()
    raise ValueError(f"Unknown music provider: {name}")
