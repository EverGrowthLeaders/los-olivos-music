from yt_music_factory.youtube import (
    YOUTUBE_ANALYTICS_SCOPE,
    YOUTUBE_READONLY_SCOPE,
    YOUTUBE_UPLOAD_SCOPE,
    _youtube_client_config_from_env,
    youtube_oauth_scopes,
    youtube_upload_scopes,
)


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


def test_upload_scope_is_decoupled_from_analytics_scopes():
    assert youtube_upload_scopes() == [YOUTUBE_UPLOAD_SCOPE]
    assert YOUTUBE_READONLY_SCOPE in youtube_oauth_scopes()
    assert YOUTUBE_ANALYTICS_SCOPE in youtube_oauth_scopes()
