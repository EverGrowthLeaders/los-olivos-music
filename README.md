# yt-music-factory

Pipeline automatizado para crear vídeos largos de música para YouTube:

1. Genera canciones originales con un proveedor por API.
2. Genera fondos con Nano Banana 2 / Gemini o usa imágenes ya creadas.
3. Ensambla un vídeo largo con FFmpeg usando imágenes estáticas o slideshow a bajo FPS.
4. Crea metadatos SEO y thumbnail.
5. Sube el MP4 a YouTube con OAuth y subida resumible.

El pipeline está pensado para funcionar en producción con claves reales, pero incluye proveedores `local` para probar todo sin gastar créditos.

---

## Decisión técnica

**Proveedor principal de música recomendado:** Google Lyria vía Gemini API.

Motivo: encaja con el stack Gemini del proyecto, permite generación musical original por prompt y mantiene una integración directa con el resto del pipeline. El código evita referencias a artistas, canciones, sellos o letras existentes y bloquea prompts con frases de imitación como `in the style of`, `sounds like`, covers, remixes o voces de terceros.

**Fallback local/assets:** el proveedor `local` sirve para pruebas sin gastar créditos, y `assets` permite usar audio propio.

**Suno:** no se automatiza en este proyecto. Los wrappers no oficiales y automatizaciones con navegador/captcha son frágiles y aumentan el riesgo de incumplir términos o perder producción.

**Fondos:** Nano Banana 2 vía Gemini API (`gemini-3.1-flash-image-preview`).

**Render:** FFmpeg con audio pre-normalizado a WAV para concatenación robusta y vídeo H.264 de imágenes fijas a 1 FPS por defecto.

---

## Instalación local

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
ymf doctor
```

FFmpeg debe estar instalado:

```bash
ffmpeg -version
ffprobe -version
```

En Debian/Ubuntu:

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg
```

---

## Probar sin gastar créditos

```bash
ymf render examples/local_demo.yaml --workdir runs --no-upload
```

Salida esperada:

```text
runs/local-demo-lofi/render/local-demo-lofi.mp4
runs/local-demo-lofi/render/local-demo-lofi.m4a
runs/local-demo-lofi/render/local-demo-lofi_thumb.jpg
runs/local-demo-lofi/youtube_metadata.json
```

También puedes ejecutar:

```bash
make test
```

---

## Reutilización, extensión y control de coste

El pipeline incluye una estrategia configurable para reducir coste sin repetir demasiado el catálogo:

- Reutiliza clips ya generados con límites por canal, uso global, cooldown, slot temporal y solapamiento.
- Extiende clips internamente con duplicación + crossfade suave de FFmpeg.
- Valida bigrams, trigrams, LCS, posición y scores de riesgo antes de renderizar/publicar.
- Guarda un `video_manifest.json` por render.
- Registra clips en `workdir/data/assets.sqlite`.
- Exporta clips generados como AAC/M4A o MP3 a 256 kbps y los puede subir a Cloudflare R2.

Comandos útiles:

```bash
ymf strategy show
ymf strategy set-profile standard
ymf strategy estimate examples/production_lofi_1h.yaml
ymf strategy validate examples/production_lofi_1h.yaml
```

Variables R2 opcionales:

```bash
R2_BUCKET=...
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
YMF_ASSET_AUDIO_FORMAT=aac   # aac | mp3
```

Más detalle: [`docs/repetition_and_extension.md`](docs/repetition_and_extension.md).

---

## Flywheel creativo

El dashboard incluye una pestaña **Flywheel** para sincronizar YouTube Analytics, rankear qué vídeos funcionan mejor, diagnosticar CTR/retención/watch time y aplicar recomendaciones a futuros renders por canal y categoría.

Cada render guarda un `creative_manifest.json` con los prompts, timeline, miniatura y metadata usados. Las recomendaciones aprobadas se guardan en `workdir/data/analytics.sqlite` y se inyectan en los siguientes prompts de esa categoría.

Más detalle: [`docs/flywheel.md`](docs/flywheel.md).

---

## Render de producción con Lyria 3 Pro + Nano Banana 2

1. Edita `.env`:

```bash
GEMINI_API_KEY=...
```

2. Ajusta el spec:

```bash
cp examples/production_lyria_1h.yaml examples/my_video.yaml
```

3. Renderiza:

```bash
ymf render examples/my_video.yaml --workdir runs --no-upload
```

4. Revisa manualmente el resultado privado antes de publicar.

---

## Usar assets manuales en vez de generar música/imágenes

```bash
ymf render examples/assets_only.yaml --workdir runs --no-upload
```

Rellena en el YAML:

```yaml
assets:
  audio_files:
    - ./assets/track01.mp3
    - ./assets/track02.mp3
  image_files:
    - ./assets/background01.png
```

---

## Subida automática a YouTube

1. Crea un proyecto en Google Cloud.
2. Habilita YouTube Data API v3.
3. Crea credenciales OAuth de tipo Desktop App.
4. Descarga `client_secret.json` y ponlo en la raíz del proyecto o define:

```bash
YOUTUBE_CLIENT_SECRETS=/ruta/client_secret.json
YOUTUBE_TOKEN_FILE=.secrets/youtube-token.json
```

5. En el spec, mantén primero `privacy_status: private`:

```yaml
youtube:
  upload: true
  privacy_status: private
  contains_synthetic_media: true
  made_for_kids: false
  notify_subscribers: false
```

6. Ejecuta:

```bash
ymf render examples/my_video.yaml --workdir runs --upload
```

La primera ejecución abre OAuth en el navegador. Las siguientes reutilizan el token.

> Nota operativa: los proyectos de YouTube Data API nuevos o no verificados pueden quedar restringidos a subir vídeos en privado hasta completar revisión/auditoría de API.

---

## YAML de trabajo

Campos clave:

```yaml
job:
  slug: lofi-focus-cyberpunk-001
  title_seed: "cozy cyberpunk deep work"
  language: en
  target_minutes: 60
category_key: focus_lofi
asset_strategy:
  profile: standard
  cross_video_reuse:
    enabled: true
    target_reuse_ratio: 0.30
  internal_extension:
    enabled: true
    preferred_extension_factor: 1.85
    preferred_crossfade_seconds: 18
music:
  provider: lyria     # local | lyria | assets
  model: lyria-realtime-exp
  track_count: 20
  track_duration_seconds: 180
  instrumental: true
images:
  provider: gemini         # local | gemini | assets
  count: 4
  aspect_ratio: "16:9"
  image_size: "2K"
video:
  resolution: "1920x1080"
  fps: 1
  visual_mode: slideshow   # single | slideshow
  image_duration_seconds: 900
  video_preset: veryfast
  audio_bitrate: 192k
seo:
  provider: gemini         # local | gemini
  primary_keyword: "lofi beats for studying"
youtube:
  upload: false
  privacy_status: private
  contains_synthetic_media: true
```

---

## Categorías incluidas

Configurable en `config/categories.yaml`:

- `focus_lofi`
- `sleep_ambient`
- `meditation_nature`
- `synthwave_gaming`
- `piano_rain`

Cada categoría incluye keywords, tags, prompt musical, prompt visual y categoría de YouTube.

---

## Producción: checklist mínimo

- Revisa que el plan del proveedor musical permite el uso concreto en YouTube y monetización.
- No uses nombres de artistas, canciones, sellos, letras existentes ni “suena como X”.
- Mantén `contains_synthetic_media: true` para música generada con IA.
- Sube primero en `private` y revisa claims, audio, descripción, thumbnail y volumen.
- Evita publicar muchos vídeos casi idénticos. Cambia música, prompts, visuales, descripción y propuesta de valor por vídeo.
- Guarda evidencias: prompts, proveedor, fecha, ID de generación, factura/licencia y metadata.

---

## Docker

```bash
docker build -t yt-music-factory .
docker run --rm -it \
  --env-file .env \
  -v "$PWD/runs:/app/runs" \
  -v "$PWD/examples:/app/examples" \
  yt-music-factory render examples/production_lyria_1h.yaml --workdir runs --no-upload
```

---

## Estructura

```text
src/yt_music_factory/
  cli.py                  # CLI: render, seo, upload, doctor, strategy
  pipeline.py             # Orquestación
  ffmpeg.py               # Concatenación, extensión y render bajo CPU
  asset_registry.py       # Registry SQLite de clips
  asset_strategy.py       # Presets, warnings y estimador
  timeline/planner.py     # Planner de reuse/extensión
  repetition/validators.py # Gate de repetición
  seo.py                  # Metadata SEO local/Gemini
  youtube.py              # Upload resumible con OAuth
  providers/music/        # local, Lyria
  providers/image/        # local, Gemini/Nano Banana 2
config/categories.yaml
examples/*.yaml
tests/*.py
```
