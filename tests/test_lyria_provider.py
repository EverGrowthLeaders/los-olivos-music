from types import SimpleNamespace

from yt_music_factory.providers.music.lyria import LyriaMusicProvider


def test_lyria_normalizes_legacy_rest_model_to_realtime():
    assert LyriaMusicProvider._normalize_model("lyria-3-pro-preview") == "models/lyria-realtime-exp"


def test_lyria_normalizes_model_prefix():
    assert LyriaMusicProvider._normalize_model("lyria-realtime-exp") == "models/lyria-realtime-exp"
    assert LyriaMusicProvider._normalize_model("models/lyria-realtime-exp") == "models/lyria-realtime-exp"


def test_lyria_audio_chunks_from_snake_case_message():
    message = SimpleNamespace(
        server_content=SimpleNamespace(audio_chunks=[SimpleNamespace(data=b"pcm bytes")])
    )

    assert LyriaMusicProvider._audio_chunks_from_message(message) == [b"pcm bytes"]


def test_lyria_audio_chunks_from_camel_case_dict_message():
    message = SimpleNamespace(
        serverContent=SimpleNamespace(audioChunks=[{"data": b"pcm dict bytes"}])
    )

    assert LyriaMusicProvider._audio_chunks_from_message(message) == [b"pcm dict bytes"]
