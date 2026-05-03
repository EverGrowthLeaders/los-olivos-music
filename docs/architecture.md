# Arquitectura de producción

## Flujo

```text
YAML spec
  ├─ category config
  ├─ music provider: assets | local | lyria
  ├─ image provider: assets | local | gemini
  ├─ SEO metadata: local | gemini
  ├─ FFmpeg audio concat + target duration
  ├─ FFmpeg still/slideshow video encode
  ├─ thumbnail
  └─ YouTube resumable upload
```

## Bajo consumo de CPU

- Vídeo a 1 FPS por defecto.
- `-tune stillimage` para H.264.
- Sin visualizadores, waveforms ni animaciones por defecto.
- Slideshow con imágenes estáticas, no vídeo generado.
- Audio transcodificado una vez a WAV 44.1 kHz/2 canales para evitar errores de concat.
- Audio final AAC, copiado al MP4 sin recodificar en la fase de vídeo.

## Por qué pre-normalizar audio

Los proveedores pueden devolver MP3/WAV con parámetros distintos. FFmpeg concat puede fallar si se mezclan códecs, sample rates o layouts. Por eso el pipeline convierte cada pista corta a WAV homogéneo y luego crea un loop de duración exacta.

## Publicación

La subida usa OAuth de Desktop App y `videos.insert` con media resumible. El JSON incluye:

- `snippet.title`
- `snippet.description`
- `snippet.tags`
- `snippet.categoryId`
- `status.privacyStatus`
- `status.selfDeclaredMadeForKids`
- `status.containsSyntheticMedia`

La publicación pública debe hacerse solo cuando el proyecto de API y el canal estén en condiciones de cumplir políticas.
