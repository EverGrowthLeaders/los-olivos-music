# Production runbook

## First-time setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "[dev]"
cp .env.example .env
ymf doctor
```

Install FFmpeg if missing.

## Generate one private video

```bash
cp examples/production_lofi_1h.yaml examples/video_001.yaml
```

Edit:

- `job.slug`
- `job.title_seed`
- `category_key`
- `music.prompt` if you do not want the category default
- `images.prompt` if you do not want the category default
- `seo.primary_keyword`
- `youtube.privacy_status: private`

Render without upload:

```bash
ymf render examples/video_001.yaml --workdir runs --no-upload
```

Review:

```text
runs/<slug>/render/<slug>.mp4
runs/<slug>/render/<slug>_thumb.jpg
runs/<slug>/youtube_metadata.json
```

## Upload after review

Set `.env`:

```bash
YOUTUBE_CLIENT_SECRETS=/absolute/path/client_secret.json
YOUTUBE_TOKEN_FILE=.secrets/youtube-token.json
```

Upload:

```bash
ymf render examples/video_001.yaml --workdir runs --upload
```

Or upload an already-rendered MP4:

```bash
ymf upload \
  runs/<slug>/render/<slug>.mp4 \
  runs/<slug>/youtube_metadata.json \
  --thumbnail runs/<slug>/render/<slug>_thumb.jpg
```

## Batch strategy

Start with one video per category and evaluate retention, search terms and claims before scaling.

Suggested cadence while validating:

1. 1 focus/lo-fi video.
2. 1 sleep/ambient video.
3. 1 meditation/nature video.
4. 1 piano/rain video.
5. Only then scale the winning category.

## Evidence log

Keep, per upload:

- YAML spec used.
- Exact prompts.
- Provider and generation timestamps.
- Provider generation IDs or returned file names when available.
- Final MP4 and metadata JSON.
- Invoice/license/plan snapshot.
- YouTube video ID.

## Operational guardrails

- Keep `contains_synthetic_media: true` for AI music/visuals.
- Avoid artist names, song titles, lyrics, labels and vocal impersonation.
- Do not set `public` automatically in early runs.
- Set `notify_subscribers: false` for batch uploads.
- Do not publish many near-identical videos.
