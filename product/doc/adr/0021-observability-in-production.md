# 0021 · Observabilidad en producción: árbol de trazas, log con nivel, métricas desglosadas y dashboard

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción · Completa los ADR 0004, 0005 y 0006

## Contexto

Desde la sesión 2 había `request_id`, un log JSON, `/metrics` con SLO y trazas opcionales. Para operar en
producción faltaba:

- Una traza raíz por pregunta con lo que se sabe al final (modelo, ruta, costo, fallback) y los tokens de cada
  llamada en LangSmith.
- Un nivel en cada línea del log, y no mezclar las líneas de arranque de uvicorn, que no son JSON.
- Desgloses por modelo, ruta y cliente, y métricas de seguridad y de negocio.
- Un tablero.
- Un runbook.
- Unir la llamada al modelo real con la petición.

## Decisión

**Trazas** (`app/tracing.py`, `AskService`):

- Cada pregunta es un árbol `ask` → `agent_run` → `model` / herramientas.
- `ask` se abre con `request_id`, `client` y `environment`, y al terminar recibe con `annotate` `model`, `route`,
  `fallback`, `steps`, `stopped`, `cached_calls`, `cost_usd` y `model_retries`.
- Cada `model` lleva `ls_model_name` y `ls_provider`, y su salida trae `usage_metadata`, para que LangSmith
  muestre tokens y costo.
- Siguen redactadas u ocultas según `HIDE_*` (ADR 0014).

**Log** (`app/logs.py`):

- Cada línea agrega `level`, `environment` y `version`.
- `warning` para los rechazos de auditoría, `model_fallback` y un circuito abierto; `error` para `error`; `info`
  para lo demás.
- El contenedor usa `--log-level warning`: en Render ya no aparecen las cuatro líneas de arranque de uvicorn, y sus
  advertencias y errores siguen saliendo.

**Métricas** (`app/metrics.py`):

- Un observador del log: cada registro pasa por `Metrics.observe`, así `/metrics` y el log cuentan lo mismo, sin
  duplicar lógica en cada lugar que audita.
- Se conserva el contrato. Se agregan `latency_p99_ms`, `by_status`, `by_model`, `by_route`, `by_client`,
  `cache_hit_rate`, `cost_per_answer_usd`, `errors_by_type`, `security` y `business` (herramientas, tope de pasos,
  fallbacks, reintentos y tasa de rechazo).

**Dashboard propio:**

- `GET /dashboard` es una página sin librerías y sin datos. Lee `/metrics` con la clave de `sessionStorage` y
  pinta con `textContent`.
- Tiene su propia CSP por hash, igual que la página principal (ADR 0016), y se actualiza cada 15 s.
- En LangSmith, el tablero se arma con los metadatos (`doc/observability.md`).

**Correlación con el AI Gateway:** cada llamada al modelo real manda `cf-aig-metadata` con el `request_id` (una
variable de contexto que fija `AskService`), para encontrar la llamada en los registros del gateway. El nombre y el
formato del encabezado siguen la documentación de Cloudflare: se confirman en la fase final, en los registros del
gateway.

**Runbook:** `doc/observability.md` responde qué pasó con una petición, por qué sube el costo, por qué hay `502`,
`503` o `429` y si hay un ataque, con umbrales de alerta.

**Scripts de evidencia que corre la persona:**

- `scripts/trace_demo.py`, con `uv run --env-file .env`: enciende LangSmith solo en su proceso, usa una clave de
  cliente aleatoria que no se muestra, y escribe los ids y el log de una petición, sin texto.
- `scripts/observability_demo.py <url>`: mezcla de tráfico y foto de `/metrics`.

## Consecuencias

- Una petición se sigue de punta a punta con un solo id: página, log, LangSmith y AI Gateway.
- `/metrics` crece pero su tamaño está acotado: dos modelos, pocas rutas, los clientes de `API_KEYS` y unos pocos
  tipos de error.
- Las métricas siguen siendo por proceso (ADR 0005 y 0019); LangSmith y el log de Render juntan todos los procesos.
- `verification/server.py` (la fábrica) sigue arrancando uvicorn con su log de acceso: eso solo afecta a las
  verificaciones, no a producción.
