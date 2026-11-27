# Evidencias

Qué prueba cada archivo, a qué criterio responde y cómo se vuelve a generar. Las evidencias no llevan claves,
tokens, datos personales reales ni texto de conversaciones. Los datos del directorio (`data/employees.json`) son
sintéticos, y las claves que aparecen son valores de ejemplo de las pruebas.

## Generadas en el equipo local

| Archivo | Qué prueba | Criterio | Cómo se genera (desde `product/`) |
| --- | --- | --- | --- |
| [`ci-local-rehearsal.txt`](ci-local-rehearsal.txt) | Los trabajos del CI en entornos limpios de Python 3.12. La imagen solo lleva las dependencias de ejecución | Arquitectura; seguridad (cadena de suministro) | Los comandos de `.github/workflows/ci.yml` en un venv limpio |
| [`load-test.txt`](load-test.txt) | 200 de 200 peticiones con 20 a la vez; el límite por cliente responde `429` sin tumbar el servicio | Arquitectura (concurrencia) | `uv run python scripts/load_test.py <url>` contra uvicorn local |
| [`security-check.txt`](security-check.txt) | 13 de 13 ataques con el resultado esperado (sin clave, inyección, secreto, CURP, cuerpo de más, cabeceras...) y sus líneas de auditoría sin clave ni IP | Seguridad | `uv run python scripts/security_check.py <url>` |
| [`metrics-sample.json`](metrics-sample.json) | `/metrics` con desgloses por código, modelo, ruta y cliente, seguridad y negocio, tras 6 peticiones conocidas | Observabilidad | `uv run python scripts/observability_demo.py <url>` |
| [`logs-request-sample.jsonl`](logs-request-sample.jsonl) | Las líneas del log de una petición de esa mezcla, unidas por su `request_id` | Observabilidad | Salida del servidor filtrada por el `request_id` |
| [`langsmith-local.txt`](langsmith-local.txt) | Modelo real, 2026-11-26: cuatro preguntas con LangSmith encendido (simple al 8B, compleja al 70B, CURP sintética redactada, inyección `400`), con `request_id`, modelo, ruta y costo para buscar cada traza | Observabilidad | `uv run --env-file .env python scripts/trace_demo.py` |
| [`logs-request-local.jsonl`](logs-request-local.jsonl) | El log de la pregunta simple de esa corrida (`tool_call`, `agent_run` y `request`), sin texto | Observabilidad | Lo escribe `trace_demo.py` |
| [`evaluate-output.txt`](evaluate-output.txt) | Línea base contra producción con el modelo simulado: misma calidad, 86.2 % menos costo | Costos | `uv run python -m verification evaluate` (desde la raíz) |
| [`cost-ablation.md`](cost-ablation.md) | Ahorro de cada palanca por separado, las descartadas y la proyección | Costos | `uv run python scripts/cost_ablation.py` |
| [`cost-real-model.md`](cost-real-model.md) | Modelo real, 2026-11-26: línea base contra palancas, −85.9 % de costo con calidad de 100 %; en la repetición, 15 de 15 llamadas desde la caché con costo 0 y p50 de 87 ms; proyección mensual | Costos | `uv run --env-file .env python scripts/cost_real_sample.py` |

## En producción (Render, 2026-11-26)

Contra `https://m4-hr-assistant.onrender.com`, con la clave de un cliente en `SMOKE_API_KEY` leída sin eco (README, «Despliegue»).

| Archivo | Resultado | Criterio | Cómo se genera |
| --- | --- | --- | --- |
| [`smoke-production.txt`](smoke-production.txt) | Prueba de humo de la fábrica `[OK]` en sus 3 casos: `/health` 200, `/ask` sin clave 401, `/ask` con clave 200 | Arquitectura | `uv run python -m verification production <url>` (desde la raíz) |
| [`metrics-render.json`](metrics-render.json) | Los 6 casos con el código esperado. `/metrics` de producción: `error_rate` 0; 3 respuestas del 8B y 1 del 70B (`keyword:compara`); `cache_hit_rate` 22 %; contadores `auth_failed`, `pii_detected`, `injection_blocked` y `secret_blocked`; `stopped_at_step_cap` 1; p95 de 2280 ms frente al SLO de 2000 | Observabilidad | `uv run python scripts/observability_demo.py <url> --label render` |
| [`load-test-render.txt`](load-test-render.txt) | 20 de 20 con 5 a la vez, sin errores de red; p50 de 609 ms y p95 de 3362 ms en el plan gratuito | Arquitectura | `uv run python scripts/load_test.py <url>` |
| [`security-check-render.txt`](security-check-render.txt) | 13 de 13 ataques con el resultado esperado contra el servicio publicado | Seguridad | `uv run python scripts/security_check.py <url>` |

**Comprobación de origen (ADR 0017), hecha el 2026-11-26.**

- Con el equipo de la persona bloqueado por claves equivocadas (`429`), una pregunta sin clave desde un celular
  en otra red recibió `401` («Clave inválida»).
- Render agrega la IP real al final de `X-Forwarded-For`, como supone `TRUSTED_PROXY_HOPS=1`: un atacante bloquea
  solo su origen.

En PowerShell, para guardar la salida en UTF-8:

```powershell
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$url = 'https://m4-hr-assistant.onrender.com'
uv run python scripts/observability_demo.py $url --label render
uv run python scripts/security_check.py $url | Out-File -Encoding utf8 doc/evidence/security-check-render.txt
uv run python scripts/load_test.py $url | Out-File -Encoding utf8 doc/evidence/load-test-render.txt
```

## Capturas

Están en [`capturas/`](capturas/), en PNG, y fueron revisadas: ninguna muestra claves, tokens, correos ni el id
de la cuenta de Cloudflare.

| Archivo | Qué muestra | Criterio |
| --- | --- | --- |
| [`pagina.png`](capturas/pagina.png) | La página del servicio en producción | Arquitectura |
| [`swagger.png`](capturas/swagger.png) | `/docs` en producción con **Authorize**, con HSTS en las cabeceras de la respuesta y sin el bloque «Curl» | Arquitectura y seguridad |
| [`dashboard.png`](capturas/dashboard.png) | `/dashboard` con métricas de producción | Observabilidad |
| [`render-live.png`](capturas/render-live.png) | El servicio **Live** en Render | Arquitectura |
| [`actions-ci.png`](capturas/actions-ci.png) | Los seis trabajos del CI en verde: `style`, `unit`, `e2e`, `docker`, `factory` y `security` | Arquitectura (capa 1) y seguridad |
| [`render-logs.png`](capturas/render-logs.png) | Render Logs con las líneas JSON del servicio | Observabilidad |
| [`render-logs-request-id.png`](capturas/render-logs-request-id.png) | Render Logs filtrado por el `request_id` de `langsmith-trace.png`: `pii_detected`, `tool_call`, `agent_run` y `request` de la misma petición | Observabilidad |
| [`langsmith-trace.png`](capturas/langsmith-trace.png) | El árbol `ask` → `agent_run` → `model` → `get_employee` → `model` en `m4-hr-assistant-prod`, con la CURP como `[REDACTED:CURP]` | Observabilidad y seguridad |
| [`langsmith-dashboard.png`](capturas/langsmith-dashboard.png) | El dashboard de LangSmith del proyecto de producción | Observabilidad |
| [`ai-gateway-overview.png`](capturas/ai-gateway-overview.png) | Resumen del AI Gateway: 125 peticiones y 0 errores; el consumo real frente a `cost_usd` | Costos |
| [`ai-gateway-logs.png`](capturas/ai-gateway-logs.png) | Registros del AI Gateway con llamadas desde la caché a 0 USD | Costos |
