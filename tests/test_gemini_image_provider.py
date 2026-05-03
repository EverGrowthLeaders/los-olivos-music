from yt_music_factory.providers.image.gemini import GeminiImageProvider


def test_gemini_image_keeps_nano_banana_2_model():
    assert GeminiImageProvider._normalize_model("gemini-3.1-flash-image-preview") == "gemini-3.1-flash-image-preview"


def test_gemini_image_payload_requests_text_and_image():
    payload = GeminiImageProvider._build_payload("city at night", "16:9", "2K")

    config = payload["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert config["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}


def test_gemini_image_normalizes_video_resolution_to_2k():
    assert GeminiImageProvider._normalize_image_size("1920x1080") == "2K"
    assert GeminiImageProvider._normalize_image_size("4k") == "4K"


def test_gemini_image_prompt_prioritizes_account_style_and_forbids_text():
    prompt = GeminiImageProvider._compose_prompt(
        "Cozy study room, warm desk lamp",
        "Channel aesthetic: austere trading terminal, graphite and green palette\n"
        "Thumbnail style: high contrast, central abstract market symbol",
    )

    assert "mandatory and has priority" in prompt
    assert "austere trading terminal" in prompt
    assert "central abstract market symbol" in prompt
    assert "Category/use-case prompt:" in prompt
    assert "no text" in prompt.lower()
    assert "filenames" in prompt
    assert prompt.index("austere trading terminal") < prompt.index("Cozy study room")
