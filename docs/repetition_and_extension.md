# Reutilización y extensión de clips

El sistema `asset_strategy` controla cuánto material musical puede repetirse y cuánto puede alargarse antes de renderizar o publicar un vídeo.

## Dos técnicas distintas

**Reutilización cross-video** usa clips ya generados en vídeos anteriores. Reduce coste, pero está limitada por ratios, cooldowns, uso por canal, uso global, orden de clips y posición temporal.

**Extensión interna** convierte un clip en una sección más larga dentro del mismo vídeo. El clip se duplica con `acrossfade` en FFmpeg y microvariación sutil en la segunda pasada. En el manifest cuenta como una sola `extended section`, no como dos clips independientes.

## Por qué intros/outros no se reutilizan

Los primeros minutos y los cierres son lo más fácil de reconocer por el oyente. Por defecto:

- El primer track, segundo track y último track deben ser nuevos.
- Los primeros 6 minutos deben ser nuevos.
- Los últimos 3 minutos deben ser nuevos.
- No se extienden clips dentro de esas ventanas.

## Bigrams y trigrams

Un bigram es un par ordenado de tracks consecutivos, por ejemplo `A -> B`.

Un trigram es un trío ordenado, por ejemplo `A -> B -> C`.

El planner rechaza secuencias que repitan bigrams o trigrams ya usados en el mismo canal. Así dos vídeos pueden compartir algún clip sin repetir la sensación de “misma playlist”.

## Risk score

El gate calcula:

- `audio_overlap_score`: clips compartidos / clips del vídeo candidato.
- `sequence_similarity_score`: mayor ratio LCS contra el historial.
- `position_similarity_score`: clips repetidos en el mismo slot o slot vecino.
- `metadata_similarity_score`: placeholder determinista hasta tener embeddings SEO.
- `visual_similarity_score`: placeholder determinista hasta tener embeddings visuales.

El total usa pesos:

- Audio: 0.35
- Secuencia: 0.25
- Posición: 0.20
- Metadata: 0.10
- Visual: 0.10

Si cualquier score supera su threshold, el vídeo se rechaza antes de renderizar/publicar.

## Perfiles

**Conservative**

- Reuse objetivo: 20%
- Reuse máximo: 30%
- Extensión objetivo: 25%
- Extensión máxima: 35%
- Mínimo fresco: 60%

**Standard**

- Reuse objetivo: 30%
- Reuse máximo: 40%
- Extensión objetivo: 35%
- Extensión máxima: 50%
- Mínimo fresco: 50%

**Aggressive**

- Reuse objetivo: 40%
- Reuse máximo: 50%
- Extensión objetivo: 45%
- Extensión máxima: 60%
- Mínimo fresco: 40%
- Muestra warning: mayor riesgo de repetición percibida.

Recomendación inicial: 20-30% de reuse, 25-35% de extensión interna y mínimo 30 minutos frescos por vídeo de 60 minutos.

## Cost estimator

El estimador compara:

- Coste base: clips necesarios sin optimización.
- Coste optimizado: clips nuevos esperados tras reuse + extensión.
- Ahorro porcentual.
- Coste proyectado por 100, 500 y 1000 vídeos.

Defaults:

- Duración de clip original: 3 min.
- Precio por generación musical: `$0.08`.
- Precio thumbnail: `$0.134`.

## Persistencia y R2

El registry de assets vive por defecto en `workdir/data/assets.sqlite`. Cada clip generado se registra con metadata, uso, slots anteriores y fingerprints.

Los clips generados también se preparan en `workdir/data/stored_clips` como AAC/M4A o MP3 a 256 kbps. Si estas variables están configuradas, se suben a Cloudflare R2:

```bash
R2_BUCKET=...
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_PUBLIC_BASE_URL=https://cdn.example.com   # opcional
YMF_ASSET_AUDIO_FORMAT=aac                   # aac | mp3
```

## CLI

```bash
ymf strategy show
ymf strategy set-profile standard
ymf strategy estimate examples/production_lofi_1h.yaml
ymf strategy validate examples/production_lofi_1h.yaml
ymf render examples/production_lofi_1h.yaml --workdir runs --no-upload
```

## Manifest

Cada render escribe `video_manifest.json` con:

- timeline completa.
- clips nuevos, reutilizados y extendidos.
- minutos frescos, reutilizados y extendidos.
- scores de riesgo.
- candidatos rechazados.
- estado final del publish gate.
