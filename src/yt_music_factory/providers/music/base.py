from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ...config import JobSpec


class MusicProvider(Protocol):
    name: str

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        """Generate music tracks and return local audio paths."""
