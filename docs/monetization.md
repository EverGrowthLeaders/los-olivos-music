# Riesgo de monetización y mitigaciones

## Riesgos principales

1. Canal demasiado repetitivo o de plantilla.
2. Música sintética sin disclosure.
3. Prompts que imitan artistas, canciones, sellos o letras.
4. Licencia insuficiente del proveedor musical.
5. Subida pública con app de YouTube Data API no auditada.

## Mitigación dentro del pipeline

- `contains_synthetic_media: true` por defecto.
- Descripción con disclosure automático.
- Prompts de categoría sin nombres de artistas ni canciones.
- Títulos y tags generados con límites.
- Atribución automática de ElevenLabs en descripción cuando se usa ese proveedor.
- Subida privada por defecto.
- `result.json` y `youtube_metadata.json` persistidos por job.

## Recomendación de canal

Un canal de una hora con fondos estáticos puede funcionar para discovery, pero para YPP conviene que cada vídeo tenga una diferencia clara:

- tracks realmente nuevos,
- prompts visuales y miniaturas diferentes,
- títulos y descripciones específicas,
- tracklist o narrativa del mood,
- series temáticas con identidad clara.

No publiques lotes grandes de vídeos casi idénticos. Prioriza calidad, coherencia y señales de autoría.
