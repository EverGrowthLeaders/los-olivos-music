from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ...config import JobSpec
from ...utils import ensure_dir


class LocalImageProvider:
    """Simple deterministic placeholder generator for tests and dry-runs."""

    name = "local"

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        ensure_dir(out_dir)
        width, height = _size_from_spec(spec.images.image_size, spec.video.width_height)
        count = max(1, spec.images.count)
        paths: list[Path] = []
        for idx in range(count):
            out = out_dir / f"local_background_{idx + 1:02d}.png"
            if out.exists() and out.stat().st_size > 0:
                paths.append(out)
                continue
            image = Image.new("RGB", (width, height), (18 + idx * 12 % 30, 20, 34))
            draw = ImageDraw.Draw(image)
            for y in range(height):
                shade = int(25 + 55 * y / max(1, height - 1))
                draw.line([(0, y), (width, y)], fill=(shade // 2, shade // 3, shade))
            image.save(out)
            paths.append(out)
        return paths


def _size_from_spec(value: str, fallback: tuple[int, int]) -> tuple[int, int]:
    if value and "x" in value.lower():
        w, h = value.lower().split("x", 1)
        return int(w), int(h)
    if value == "2K":
        return 2048, 1152
    if value == "4K":
        return 3840, 2160
    return fallback
