import pytest

from yt_music_factory.prompt_safety import assert_prompt_is_licensing_safe


def test_prompt_safety_rejects_artist_imitation_language():
    with pytest.raises(ValueError):
        assert_prompt_is_licensing_safe("lofi beat in the style of a famous artist")


def test_prompt_safety_accepts_genre_mood_instruments():
    assert_prompt_is_licensing_safe("warm lo-fi instrumental, dusty drums, mellow piano, 76 bpm")
