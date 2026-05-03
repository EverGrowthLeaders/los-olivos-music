from pathlib import Path

from yt_music_factory.config import ChannelStyleConfig, category_for, channel_style_prompt, load_categories, load_spec
from yt_music_factory.seo import VideoChapter, _with_chapters, build_local_metadata


def test_load_demo_spec_and_metadata():
    spec = load_spec(Path("examples/local_demo.yaml"))
    categories = load_categories(Path("config/categories.yaml"))
    category = category_for(spec, categories)
    metadata = build_local_metadata(spec, category)

    assert spec.job.slug == "local-demo-lofi"
    assert metadata.category_id == "10"
    assert len(metadata.title) <= 100
    assert metadata.contains_synthetic_media is True
    assert "disclosure:" not in metadata.description.lower()
    assert metadata.tags


def test_resolution_validation():
    spec = load_spec(Path("examples/local_demo.yaml"))
    assert spec.video.width_height == (1280, 720)


def test_metadata_uses_supplied_chapters():
    spec = load_spec(Path("examples/local_demo.yaml"))
    categories = load_categories(Path("config/categories.yaml"))
    category = category_for(spec, categories)
    metadata = build_local_metadata(
        spec,
        category,
        chapters=[
            VideoChapter(start_seconds=0, title="Track 01"),
            VideoChapter(start_seconds=180, title="Track 02"),
        ],
    )

    assert "Chapters:" in metadata.description
    assert "00:00 Track 01" in metadata.description
    assert "03:00 Track 02" in metadata.description
    assert metadata.to_json()["chapters"][1]["start_seconds"] == 180


def test_chapter_cleanup_removes_duplicate_model_timestamps():
    description = """A calm focus mix.

00:00 Track 01
03:00 Track 02
06:00 Track 03

#lofi #focus

Chapters:
00:00 Track 01
03:00 Track 02
06:00 Track 03
"""

    result = _with_chapters(
        description,
        [
            VideoChapter(start_seconds=0, title="Track 01"),
            VideoChapter(start_seconds=180, title="Track 02"),
            VideoChapter(start_seconds=360, title="Track 03"),
        ],
    )

    assert result.count("00:00 Track 01") == 1
    assert result.count("Chapters:") == 1


def test_account_style_no_longer_adds_global_music_direction():
    prompt = channel_style_prompt(
        ChannelStyleConfig(theme="Focus channel", aesthetic="Premium calm"),
        media="music",
    )

    assert "Focus channel" in prompt
    assert "Sonic identity" not in prompt
