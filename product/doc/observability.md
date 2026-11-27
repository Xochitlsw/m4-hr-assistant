# Observabilidad y runbook del HR Assistant

Cómo ver lo que hace el sistema en producción y cómo depurar un problema concreto. Las decisiones están en los ADR
0004, 0005, 0006, 0014 y 0021.

## 1. Las tres fuentes y cómo se conectan

Todo se une por el **`request_id`**: la página lo muestra como «Id de soporte», viaja en `X-Request-ID` (el cliente
puede mandar el suyo), está en cada línea del log, en los metadatos de cada traza y, para el modelo real, en los
metadatos del AI Gateway (`cf-aig-metadata`).

| Fuente | Qué contesta | Dónde |
| --- | --- | --- |
| Log JSON | Qué pasó con una petición, quién la hizo, qué se rechazó y por qué | Salida estándar; en Render, **Logs** |
| `/metrics` y `/dashboard` | Cómo está este proceso: volumen, errores, latencia frente al SLO, costo, seguridad y negocio | `GET /metrics` (con clave) y `GET /dashboard` (la página pide la clave) |
| Trazas en LangSmith | Cómo razonó el agente: cada llamada al modelo y cada herramienta, con tokens y costo | Proyecto `m4-hr-assistant-dev` (local) o `-prod` (Render) |

## 2. El log

Una línea JSON por evento. Siempre van primero `ts`, `service`, `event` y `request_id`, y después `level`
(`info`, `warning` o `error`), `environment` y `version`. Nunca lleva el texto de preguntas o respuestas, claves,
valores de datos personales ni la IP (va una huella HMAC). El contenedor corre uvicorn con `--no-access-log` y
`--log-level warning`, así que en Render todas las líneas son JSON.

| `event` | Cuándo | Campos clave |
| --- | --- | --- |
| `request` | Cada petición | `client`, `method`, `path`, `status`, `latency_ms` |
| `agent_run` | Cada respuesta del agente | `model`, `route`, `steps`, `tools`, tokens, `cached_calls`, `cost_usd`, `stopped`, `model_retries` |
| `audit` | Acciones relevantes (13; ADR 0016 y 0017) | `action` y sus campos; nivel `warning` si es un rechazo |
| `model_fallback` | El modelo grande falló y respondió el pequeño | `from_model`, `to_model`, `error_type` |
| `model_circuit` | Un circuito cambió de estado | `model`, `state` (`open` es `warning`) |
| `error` | Una falla (`500`, `502`, `503`) | `error_type`, sin mensaje ni traza |

## 3. Las métricas

`GET /metrics` mantiene el contrato (`requests`, `error_rate`, `latency_ms` con p50 y p95, `tokens`, `slo`,
`cost_usd`) y agrega:

- `latency_p99_ms`; `by_status` (peticiones por código).
- `by_model` (respuestas, tokens y costo), `by_route` (qué decidió el enrutador, incluido `+fallback`) y
  `by_client` (respuestas, costo, `429` por límite y por presupuesto).
- `cache_hit_rate` (llamadas que contestó la caché del gateway) y `cost_per_answer_usd`.
- `errors_by_type`; `security` (cuántas veces pasó cada acción de seguridad: claves fallidas, límites,
  inyecciones, secretos, datos personales, salidas rechazadas o limpiadas).
- `business`: respuestas, uso de cada herramienta, respuestas cortadas por el tope de pasos, fallbacks,
  reintentos del modelo y `rejected_rate` (proporción de `4xx`).

Son las de **este proceso** (cada worker tiene las suyas; `pid` dice cuál contestó) y se reinician con él. El
`/dashboard` las muestra en tarjetas y tablas, y se actualiza cada 15 s.

## 4. Las trazas

Con `LANGSMITH_TRACING=true` y una clave Tracing Only, cada pregunta es un árbol:

```
ask (chain)                          metadatos: request_id, client, environment, model, route,
└── agent_run (chain)                           fallback, steps, stopped, cached_calls,
    ├── model (llm)                             cost_usd, model_retries · tag hr-assistant
    ├── get_employee / search_by_department (tool)
    └── model (llm)                  metadatos: ls_model_name, ls_provider, usage_metadata (tokens)
```

Si hubo fallback, `ask` tiene dos `agent_run`: el que falló y el del modelo pequeño. Una inyección o un secreto
dejan `ask` con su error y sin `agent_run`. Las trazas van **redactadas** (`HIDE_*=redact`): se ve la
conversación con los datos personales y los secretos reemplazados por `[REDACTED:TIPO]` (ADR 0014).

**Dashboard en LangSmith** (lo arma la persona una vez):

1. Proyecto `m4-hr-assistant-prod` > pestaña **Monitor**: trazas por hora, latencia p50/p99, errores, tokens y
   costo.
2. **Dashboards > New dashboard**, con gráficas de la latencia y del costo agrupadas por los metadatos `model`,
   `route` y `client`, y de las trazas con error.
3. Filtros guardados: `metadata.request_id = <id>` (una petición), `metadata.fallback = true`, y trazas con
   error.

## 5. Runbook: preguntas frecuentes

**¿Qué pasó con la petición X?** (la persona da el «Id de soporte»)
1. Render > Logs: busca el id. Verás `request` (código y latencia) y, según el caso, `audit` (qué se rechazó y por
   qué), `agent_run` (modelo, ruta, herramientas, costo) o `error`.
2. LangSmith: filtro `metadata.request_id = <id>`. El árbol muestra cada paso del agente, ya redactado.
3. Si usó el modelo real: en Cloudflare > AI Gateway > Logs, los metadatos de la llamada traen el mismo
   `request_id`.

**¿Por qué subió el costo?**
1. `/metrics`: compara `by_model` (¿más respuestas del modelo grande?), `by_route` (¿preguntas largas o con
   palabras clave?) y `by_client` (¿un cliente gasta más?).
2. Si `cache_hit_rate` bajó, revisa `CACHE_TTL_SECONDS`.
3. `business.model_retries` y `fallbacks` altos indican problemas del proveedor.
4. Los clientes que agotan su presupuesto aparecen en `by_client.budget_exceeded`.

**¿Por qué hay `502` o `503`?**
1. Log: `error` con `ModelUnavailable` (falló tras sus reintentos) o `ModelCircuitOpen` (circuito abierto, `503`
   con `Retry-After`).
2. `model_circuit` con `state=open` dice desde cuándo y qué modelo.
3. `/ready` responde `503` si todos los circuitos están abiertos.
4. Revisa la cuota diaria de Workers AI y los registros del AI Gateway. Con el pequeño sano, las preguntas
   complejas siguen respondiendo por fallback (`route` con `+fallback`).

**¿Por qué una persona recibe `429`?** Por el `detail` y por `audit`:
- `rate_limited`: pasó de `RATE_LIMIT_PER_MINUTE`.
- `budget_exceeded`: agotó su presupuesto del día.
- `auth_throttled`: su origen acumuló claves equivocadas; mismo `origin` que sus `auth_failed`.

**¿Nos están atacando?**
1. `/metrics` > `security`: picos de `auth_failed`, `auth_throttled`, `injection_blocked` o `secret_blocked`.
2. En el log, filtra `level=warning` y agrupa por `action` y por `origin` o `client`.
3. Si una clave de cliente se filtró, rótala (`security.md`, sección 6).

**¿La calidad bajó?** `business.stopped_at_step_cap` alto: el agente no termina en `AGENT_MAX_STEPS`. Las
`output_rejected` del log dicen qué parte del protocolo falla. `verification evaluate` mide la calidad contra la
línea base.

## 6. Umbrales de alerta sugeridos

| Señal | Umbral | Dónde |
| --- | --- | --- |
| Latencia | `slo.met = false` (p95 ≥ `SLO_P95_MS`) | `/metrics`, dashboard, LangSmith Monitor |
| Errores | `error_rate` > 5 % | `/metrics`, dashboard |
| Modelo | Cualquier `model_circuit` con `state=open`; `/ready` en `503` | Log, `/ready` |
| Costo | Costo del día > suma de los presupuestos de los clientes; un cliente con `budget_exceeded` | `/metrics` `by_client` |
| Caché | `cache_hit_rate` en 0 con `CACHE_TTL_SECONDS` > 0 y preguntas repetidas (ADR 0022) | `/metrics`, dashboard |
| Abuso | `auth_throttled` > 0 o `injection_blocked` creciendo | `/metrics` `security` |

En el plan gratuito no hay un sistema de alertas: los umbrales se revisan en el dashboard y en LangSmith. Un
monitor externo (por ejemplo, alertas de LangSmith o un chequeo de `/ready`) es la mejora siguiente.

## 7. La capa 1: la fábrica

La trazabilidad de los agentes de la fábrica (ADR 0020) es su bitácora (`hooks/activity_log.py` →
`output/activity.jsonl` → `scripts/pipeline.py`). En cada cambio, el trabajo `factory` del CI deja en GitHub
Actions el resultado de cada verificación.

## 8. Scripts de evidencia

| Script | Qué hace | Necesita |
| --- | --- | --- |
| `scripts/observability_demo.py <url>` | Mezcla de 6 peticiones y foto de `/metrics` en `doc/evidence/metrics-<label>.json` | La clave de un cliente en `SMOKE_API_KEY` |
| `scripts/trace_demo.py` | 4 preguntas con LangSmith encendido; ids y log en `doc/evidence/` | `uv run --env-file .env` con las claves de LangSmith (y de Cloudflare sin `--fake`) |
| `scripts/load_test.py <url>` | Carga concurrente | `SMOKE_API_KEY` |
| `scripts/security_check.py <url>` | 13 casos de ataque | `SMOKE_API_KEY` |
| `scripts/cost_ablation.py` | Ablación de las palancas con el modelo simulado; `doc/evidence/cost-ablation.md` | Nada |
| `scripts/cost_real_sample.py` | Línea base, palancas y caché con el modelo real; `doc/evidence/cost-real-model.md` | `uv run --env-file .env` con las credenciales de Cloudflare |
