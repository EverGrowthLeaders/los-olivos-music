from pathlib import Path

from yt_music_factory.ffmpeg import write_image_manifest


def test_image_manifest_repeats_last_file(tmp_path: Path):
    images = [tmp_path / "a.png", tmp_path / "b.png"]
    for path in images:
        path.write_bytes(b"fake")
    manifest = write_image_manifest(images, target_seconds=10, image_duration_seconds=4, manifest_path=tmp_path / "images.ffconcat")
    text = manifest.read_text()

    assert text.startswith("ffconcat version 1.0")
    assert text.count("duration") == 3
    assert text.strip().endswith("b.png'") or text.strip().endswith("a.png'")
