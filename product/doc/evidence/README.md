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
| [`langsmith-local.txt`](langsmith-local.txt) | Cuatro preguntas con LangSmith encendido: `request_id`, modelo, ruta y costo para buscar cada traza | Observabilidad | `uv run --env-file .env python scripts/trace_demo.py` |
| [`logs-request-local.jsonl`](logs-request-local.jsonl) | El log de la pregunta simple de esa corrida | Observabilidad | Lo escribe `trace_demo.py` |
| [`evaluate-output.txt`](evaluate-output.txt) | Línea base contra producción con el modelo simulado: misma calidad, 86.2 % menos costo | Costos | `uv run python -m verification evaluate` (desde la raíz) |
| [`cost-ablation.md`](cost-ablation.md) | Ahorro de cada palanca por separado, las descartadas y la proyección | Costos | `uv run python scripts/cost_ablation.py` |
| [`cost-real-model.md`](cost-real-model.md) | Línea base, palancas y caché con el modelo real: calidad, costo, tokens, `HIT` y latencia | Costos | `uv run --env-file .env python scripts/cost_real_sample.py` |

## Contra Render (fase final)

Con el servicio despierto y la clave de un cliente en `SMOKE_API_KEY`, leída sin eco (README, «Despliegue»). En
PowerShell, para guardar la salida en UTF-8:

```powershell
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$url = 'https://m4-hr-assistant.onrender.com'
uv run python scripts/observability_demo.py $url --label render
uv run python scripts/security_check.py $url | Out-File -Encoding utf8 doc/evidence/security-check-render.txt
uv run python scripts/load_test.py $url | Out-File -Encoding utf8 doc/evidence/load-test-render.txt
```

| Archivo | Qué prueba | Criterio |
| --- | --- | --- |
| `metrics-render.json` | `/metrics` de producción tras una mezcla conocida | Observabilidad |
| `security-check-render.txt` | Los 13 ataques contra el servicio publicado, con la comprobación de origen del ADR 0017 | Seguridad |
| `load-test-render.txt` | Carga corta contra el plan gratuito | Arquitectura |

## Capturas (las toma la persona)

Van en `doc/evidence/capturas/`, en PNG. Antes de guardar cada una, revisa que no se vea ninguna clave, token,
correo ni el panel **Environment** de Render.

| Archivo | Qué muestra | Cuidado |
| --- | --- | --- |
| `render-live.png` | El servicio **Live** en Render, con su URL y el último despliegue | No abrir Environment |
| `actions-ci.png` | Los seis trabajos del CI en verde, incluidos `factory` y `security` | — |
| `swagger.png` | `/docs` con el candado de **Authorize** y una respuesta de `POST /ask` | Tomarla con el diálogo de Authorize cerrado: muestra la clave |
| `langsmith-trace.png` | El árbol `ask` → `agent_run` → `model` → herramienta → `model` de una pregunta real, filtrado por su `request_id`, con metadatos y texto redactado | — |
| `langsmith-dashboard.png` | Latencia y costo agrupados por `model` o `route` | — |
| `dashboard.png` | `/dashboard` con datos de producción | El campo de la clave vacío o fuera del recorte |
| `render-logs-request-id.png` | Render Logs filtrado por el mismo `request_id` de la traza | — |
| `cloudflare-neurons.png` | Panel de Workers AI con las neuronas del día: valida el consumo real frente a `cost_usd` | Recortar la URL, que lleva el id de la cuenta |
| `ai-gateway-logs.png` | Registros del AI Gateway con el `request_id` en los metadatos y una llamada `HIT` de la caché | Recortar la URL |
