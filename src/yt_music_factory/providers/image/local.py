from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
            for i in range(16):
                x0 = int(width * (i / 16))
                radius = int(60 + 20 * math.sin(i + idx))
                draw.ellipse(
                    [x0 - radius, height // 2 - radius, x0 + radius, height // 2 + radius],
                    outline=(80 + idx * 20, 70 + i * 4, 130),
                    width=2,
                )
            label = f"{category.get('label', 'AI Music')} · {spec.job.slug}"
            font = _font(size=max(20, width // 48))
            draw.text((40, height - 80), label, font=font, fill=(235, 235, 245))
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


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()
