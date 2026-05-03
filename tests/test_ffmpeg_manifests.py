from pathlib import Path

from yt_music_factory.ffmpeg import build_audio_chapters, write_image_manifest


def test_image_manifest_repeats_last_file(tmp_path: Path):
    images = [tmp_path / "a.png", tmp_path / "b.png"]
    for path in images:
        path.write_bytes(b"fake")
    manifest = write_image_manifest(images, target_seconds=10, image_duration_seconds=4, manifest_path=tmp_path / "images.ffconcat")
    text = manifest.read_text()

    assert text.startswith("ffconcat version 1.0")
    assert text.count("duration") == 3
    assert text.strip().endswith("b.png'") or text.strip().endswith("a.png'")


def test_audio_chapters_follow_tracks_and_loops(tmp_path: Path, monkeypatch):
    audio = [tmp_path / "a.wav", tmp_path / "b.wav"]
    for path in audio:
        path.write_bytes(b"fake")

    def fake_duration(path: Path) -> float:
        return 30 if path.name == "a.wav" else 45

    monkeypatch.setattr("yt_music_factory.ffmpeg.ffprobe_duration", fake_duration)

    chapters = build_audio_chapters(audio, target_seconds=100)

    assert [(chapter.start_seconds, chapter.title) for chapter in chapters] == [
        (0, "Track 01"),
        (30, "Track 02"),
        (75, "Track 01 (loop 2)"),
    ]
    assert chapters[-1].duration_seconds == 25
