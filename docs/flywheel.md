# Creative Flywheel

El flywheel cruza tres capas:

1. **Rendimiento real de YouTube**: vistas, CTR de miniatura, retención, watch time, engagement y suscriptores.
2. **Contexto creativo del render**: categoría, prompt musical, prompt visual, miniatura, metadata, timeline y estrategia de reutilización.
3. **Aprendizaje aplicado**: recomendaciones aprobadas desde el dashboard que se inyectan en futuros prompts de esa categoría y canal.

## Requisitos

La conexión OAuth de YouTube debe incluir:

- `youtube.upload`
- `youtube.readonly`
- `yt-analytics.readonly`

Si el token se creó antes de existir el flywheel, reconecta YouTube desde **Configuración**. El dashboard avisará si el token permite subir vídeos pero no leer Analytics.

La lectura monetaria es opcional:

```bash
YOUTUBE_ANALYTICS_MONETARY=true
```

Esto añade el scope `yt-analytics-monetary.readonly`.

## Flujo operativo

1. Genera y sube vídeos normalmente.
2. Entra en **Flywheel** y pulsa **Sincronizar YouTube**.
3. Revisa el ranking por score ponderado.
4. Mira el diagnóstico:
   - CTR alto + retención baja: packaging promete algo que el contenido no mantiene.
   - Retención alta + CTR bajo: canción funciona, miniatura/título no convierten.
   - Score alto: patrón candidato a ganador.
5. Aplica solo recomendaciones que tengan sentido.

Cuando una recomendación se aplica, queda guardada en `workdir/data/analytics.sqlite` como perfil de aprendizaje por canal y categoría.

## Cómo se re-alimenta el sistema

Antes de generar música e imágenes, el pipeline carga el perfil de aprendizaje activo para `channel_id + category_key`.

La guía aprobada puede añadirse a:

- `music.prompt`
- `images.prompt`
- `channel_style.thumbnail_style`

Así se preserva el control humano: el sistema propone y el operador decide qué aprendizaje entra en producción.

## Archivos generados

Cada render guarda:

- `creative_manifest.json`: prompt, categoría, estilo, timeline, miniatura y metadata usados.
- `video_manifest.json`: timeline de clips, reutilización, extensión y riesgo de repetición.
- `result.json`: rutas finales y `youtube_video_id` si se ha subido.

La base de datos de métricas vive en:

```text
workdir/data/analytics.sqlite
```

## Score

El score va de 0 a 100 y pondera:

- Watch time por impresión: 30%
- CTR de miniatura: 25%
- Retención media: 20%
- Suscriptores ganados: 10%
- Engagement: 10%
- Revenue: 5%

La confianza crece con impresiones, vistas y edad del vídeo. Un vídeo recién publicado puede aparecer con diagnóstico de calentamiento aunque tenga señales buenas.
