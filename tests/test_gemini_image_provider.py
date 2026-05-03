from yt_music_factory.providers.image.gemini import GeminiImageProvider


def test_gemini_image_normalizes_legacy_preview_model():
    assert (
        GeminiImageProvider._normalize_model("gemini-3.1-flash-image-preview")
        == "gemini-3-pro-image-preview"
    )


def test_gemini_image_payload_requests_text_and_image():
    payload = GeminiImageProvider._build_payload("city at night", "16:9", "2K")

    config = payload["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert config["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}
