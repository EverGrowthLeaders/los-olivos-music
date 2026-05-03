from yt_music_factory.youtube import _youtube_client_config_from_env


def test_youtube_client_config_can_be_built_from_env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret-value")

    config = _youtube_client_config_from_env()

    assert config is not None
    assert config["installed"]["client_id"] == "client.apps.googleusercontent.com"
    assert config["installed"]["client_secret"] == "secret-value"
    assert config["installed"]["redirect_uris"] == ["http://localhost"]


def test_youtube_client_config_requires_id_and_secret(monkeypatch):
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret-value")

    assert _youtube_client_config_from_env() is None
