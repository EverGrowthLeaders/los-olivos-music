from pathlib import Path

import pytest

from yt_music_factory.pipeline import run_pipeline
from yt_music_factory.utils import require_binary


def test_local_pipeline_renders_short_video(tmp_path: Path):
    try:
        require_binary("ffmpeg")
        require_binary("ffprobe")
    except RuntimeError as exc:
        pytest.skip(str(exc))

    result = run_pipeline(Path("examples/local_demo.yaml"), workdir=tmp_path, upload=False)

    assert result.final_audio.exists()
    assert result.final_audio.stat().st_size > 0
    assert result.final_video.exists()
    assert result.final_video.stat().st_size > 0
    assert result.thumbnail.exists()
    assert result.metadata_path.exists()
    assert result.youtube_video_id is None
