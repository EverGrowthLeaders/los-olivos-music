from pathlib import Path

import pytest

from yt_music_factory.ffmpeg import extend_clip_with_crossfade, ffprobe_duration
from yt_music_factory.utils import require_binary, run_cmd


def _synthetic_audio(path: Path, seconds: int = 6) -> Path:
    require_binary("ffmpeg")
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
            f"sine=frequency=330:duration={seconds}:sample_rate=44100",
            "-ac",
            "2",
            str(path),
        ],
        quiet=True,
    )
    return path


def test_extend_clip_with_crossfade_outputs_readable_audio(tmp_path: Path):
    try:
        require_binary("ffmpeg")
        require_binary("ffprobe")
    except RuntimeError as exc:
        pytest.skip(str(exc))
    src = _synthetic_audio(tmp_path / "src.wav")
    out = tmp_path / "extended.m4a"

    result = extend_clip_with_crossfade(src, out, target_duration_seconds=10, crossfade_seconds=2, micro_variation=True)

    assert result.path.exists()
    assert result.duration_seconds == pytest.approx(10, abs=0.35)
    assert ffprobe_duration(out) == pytest.approx(10, abs=0.35)
    assert any("acrossfade" in item for item in result.filters_used)
    assert any("equalizer" in item for item in result.filters_used)


def test_crossfade_seconds_affects_available_duration(tmp_path: Path):
    try:
        require_binary("ffmpeg")
        require_binary("ffprobe")
    except RuntimeError as exc:
        pytest.skip(str(exc))
    src = _synthetic_audio(tmp_path / "src.wav", seconds=4)

    short_crossfade = extend_clip_with_crossfade(src, tmp_path / "short.m4a", 20, 1, False)
    long_crossfade = extend_clip_with_crossfade(src, tmp_path / "long.m4a", 20, 3, False)

    assert short_crossfade.duration_seconds > long_crossfade.duration_seconds
    assert short_crossfade.duration_seconds == pytest.approx(7, abs=0.35)
    assert long_crossfade.duration_seconds == pytest.approx(5, abs=0.35)
