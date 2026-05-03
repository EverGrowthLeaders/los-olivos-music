from pathlib import Path

from yt_music_factory.config import category_for, load_categories, load_spec
from yt_music_factory.seo import build_local_metadata


def test_load_demo_spec_and_metadata():
    spec = load_spec(Path("examples/local_demo.yaml"))
    categories = load_categories(Path("config/categories.yaml"))
    category = category_for(spec, categories)
    metadata = build_local_metadata(spec, category)

    assert spec.job.slug == "local-demo-lofi"
    assert metadata.category_id == "10"
    assert len(metadata.title) <= 100
    assert metadata.contains_synthetic_media is True
    assert "synthetically generated" in metadata.description.lower()
    assert metadata.tags


def test_resolution_validation():
    spec = load_spec(Path("examples/local_demo.yaml"))
    assert spec.video.width_height == (1280, 720)
