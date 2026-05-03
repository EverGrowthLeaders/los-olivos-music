# Research and implementation decisions

## Goal

Create a reproducible pipeline for long-form YouTube music videos with minimal manual work:

1. Generate or ingest original music.
2. Generate or ingest still backgrounds.
3. Render a low-CPU one-hour MP4.
4. Generate SEO metadata and thumbnail.
5. Upload via YouTube Data API.

## Decision matrix

| Area | Decision | Why |
| --- | --- | --- |
| Primary music provider | Google Lyria 3 Pro via Gemini API | Official API, strong full-song structure, 44.1/48 kHz stereo output depending on endpoint/docs, best fit when the project already uses Gemini/Nano Banana. |
| Local fallback | Built-in local provider and user assets | Keeps tests, previews, and manual uploads working without paid music credits. |
| Rejected automation | Unofficial Suno wrappers/browser automation | No official public API path was found; captcha/browser automation is fragile and terms-sensitive. |
| Visual provider | Gemini API / Nano Banana 2 | Official API path and high-throughput model ID for image generation. |
| Render engine | FFmpeg | Stable, scriptable, cheap CPU usage with static images and low FPS. |
| Upload | YouTube Data API v3 | Official upload path, OAuth, resumable uploads, metadata/status fields. |

## Production stance

- Use `lyria` as the default for quality and direct prompt-to-track control when Gemini API access is available.
- Use `local` or `assets` when you need a dry run, a smoke test, or manually supplied audio.
- Keep uploads private first. Review Content ID, audio quality, metadata, thumbnail and synthetic-content flags before publication.
- Keep every generated video materially distinct. Do not publish near-duplicate hour-long templates with only tiny prompt changes.

## Prompt restrictions implemented in code

The project blocks common risky prompt phrases:

- `in the style of`
- `sounds like`
- `sing like`
- `voice of`
- `cover of`
- remix/copy instructions targeting a song, band, artist, album or lyrics

This is not legal advice and does not prove that a prompt is safe, but it catches the most common avoidable mistakes.

## Limits not solved by code

- Provider account limits, model availability, pricing and usage rights can change.
- YouTube API projects may need verification/audit before public uploads through the API.
- YouTube monetization is never guaranteed; originality, review outcomes and channel history matter.
- External provider calls were not executed in this build because no API keys/OAuth credentials were supplied. Local end-to-end rendering was tested.
