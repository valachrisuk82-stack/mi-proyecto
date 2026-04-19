# NEXUS PRO ELITE — Estado del Proyecto

## Archivos activos
- `nexus_server_elite.py` — servidor principal (Railway)
- `nexus_apex.html` — frontend principal
- `nexus_users.db` — base de datos

## Railway
- URL: mi-proyecto-production-29a8.up.railway.app
- GitHub: github.com/valachrisuk82-stack/mi-proyecto
- Procfile: `web: python nexus_server_elite.py`

## Lo que está funcionando
- Telegram alertas ✅ (min_confidence = 62)
- ML Score + Trailing Stop ✅
- Caché klines 20s server + 15s frontend ✅
- multi_tf_analysis paralelo ✅
- Variables de entorno Railway ✅
- import os ✅

## Pendiente
- Mejorar señales ML (muy conservador, casi siempre WAIT)
- Verificar error 500 en /api/analyze/

## Regla de oro
Editar SIEMPRE directo en el proyecto, nunca descargar archivos.
Después de cada cambio: git add . && git commit -m "descripcion" && git push
