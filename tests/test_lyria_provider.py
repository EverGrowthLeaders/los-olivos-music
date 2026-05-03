import base64

from yt_music_factory.providers.music.lyria import LyriaMusicProvider


def test_lyria_extracts_audio_and_text():
    audio = b"fake mp3 bytes"
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "generated structure"},
                        {"inlineData": {"mimeType": "audio/mpeg", "data": base64.b64encode(audio).decode()}},
                    ]
                }
            }
        ]
    }

    audio_bytes, text_parts = LyriaMusicProvider._extract_audio_and_text(payload)

    assert audio_bytes == audio
    assert text_parts == ["generated structure"]


def test_lyria_wav_payload_sets_response_mime_type():
    payload = LyriaMusicProvider._build_payload("ambient track", "wav")

    assert payload["generationConfig"]["responseModalities"] == ["AUDIO", "TEXT"]
    assert payload["generationConfig"]["responseMimeType"] == "audio/wav"
