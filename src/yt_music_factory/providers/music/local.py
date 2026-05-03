from __future__ import annotations

import math
from pathlib import Path

from ...config import JobSpec
from ...utils import ensure_dir, require_binary, run_cmd


class LocalMusicProvider:
    """Tiny deterministic audio generator for tests and local dry-runs.

    This is deliberately not a production music model. It lets the rest of the pipeline be tested
    without spending API credits.
    """

    name = "local"

    def generate(self, spec: JobSpec, category: dict, out_dir: Path) -> list[Path]:
        require_binary("ffmpeg")
        ensure_dir(out_dir)
        count = max(1, spec.music.track_count)
        duration = max(1, spec.music.track_duration_seconds)
        paths: list[Path] = []
        for idx in range(count):
            freq = 196 + (idx * 37) % 220
            out = out_dir / f"local_track_{idx + 1:02d}.wav"
            fade_out_start = max(0, duration - 1)
            # A soft sine plus low volume keeps demo renders small and non-jarring.
            run_cmd(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={freq}:duration={duration}:sample_rate=44100",
                    "-af",
                    f"volume=0.12,afade=t=in:st=0:d={min(1, duration / 4):.2f},"
                    f"afade=t=out:st={fade_out_start}:d={min(1, duration / 4):.2f}",
                    "-ac",
                    "2",
                    str(out),
                ],
                quiet=True,
            )
            paths.append(out)
        return paths
