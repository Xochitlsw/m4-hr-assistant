# HR Assistant

Asistente de Recursos Humanos que responde preguntas sobre empleados desde una página web. Es una API FastAPI
**sin estado** con un agente de bucle con tope y dos herramientas de solo lectura (`get_employee`,
`search_by_department`) sobre `data/employees.json`.

- **Rastreable**: `request_id`, log JSON, auditoría, `/metrics` con SLO y trazas opcionales en LangSmith.
- **Con costo visible**: cada respuesta dice qué modelo usó, por qué y cuánto costó, con cuatro palancas de costo
  medidas contra una línea base.
- **Listo para producción**: clave por cliente, límite de peticiones, reglas de compliance que impiden arrancar mal
  configurado, contenedor, pipeline de CI y Blueprint de Render.

Arquitectura y decisiones: [`doc/architecture.md`](doc/architecture.md), [`doc/adr/`](doc/adr/) y
[`doc/compliance-checklist.md`](doc/compliance-checklist.md).

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/).
- Las dependencias de `requirements.txt` (en la fábrica, el mismo entorno de la raíz).
- Para el modelo real: una cuenta de Cloudflare con Workers AI y un AI Gateway (por omisión, `default`).
- Para publicar: una cuenta de GitHub y otra de Render. Docker es opcional: el CI construye la imagen.

## Cómo correrlo

`POST /ask` y `GET /metrics` piden una clave. En local, define un cliente con su clave en `API_KEYS` (la forma es
`cliente:clave`) y arranca desde esta carpeta (`product/`), con el modelo simulado (sin red ni costo):

```bash
API_KEYS="local:<una-clave-larga-que-inventes>" uv run uvicorn app.main:app --no-access-log      # Bash
```

```powershell
$env:API_KEYS = "local:<una-clave-larga-que-inventes>"                                              # PowerShell
uv run uvicorn app.main:app --no-access-log
```

`--no-access-log` apaga el log de acceso de uvicorn, que escribe líneas que no son JSON; el evento `request` ya
registra cada petición.

Abre http://127.0.0.1:8000, escribe en **Clave de acceso** la parte que va después de `local:` y pregunta, por
ejemplo, «¿Quién es EMP-0004?» o «¿Quiénes trabajan en Finanzas?». Cada respuesta muestra un **Id de soporte** (el
`request_id`). La página guarda la clave solo en `sessionStorage`: se borra al cerrar la pestaña.

### Con el modelo real

1. Copia la plantilla: `cp .env.example .env` (en PowerShell: `Copy-Item .env.example .env`).
2. En tu editor, pon `MODEL_PROVIDER=gateway`, tu `CF_ACCOUNT_ID`, tu `CF_API_TOKEN` y un `API_KEYS`. Nunca subas
   `.env` ni pegues sus valores en un chat.
3. Arranca con esas variables:

```bash
uv run --env-file .env uvicorn app.main:app --no-access-log
```

El plan gratuito de Workers AI tiene una cuota diaria: prueba primero con el modelo simulado. Si el gateway no
responde o se agota la cuota, `POST /ask` responde `502 {"detail": "model unavailable", "request_id": ...}`.

## API

| Método y ruta | Clave | Qué hace |
| --- | --- | --- |
| `GET /` | No | La página web |
| `GET /health` | No | `{"status": "ok", "pid": ...}` |
| `GET /docs` | No | Documentación interactiva (Swagger), con el esquema de seguridad `X-API-Key` |
| `POST /ask` | `X-API-Key` | Cuerpo `{"question": "...", "history": [...]}`; responde `answer`, `steps`, `tools_used`, `input_tokens`, `output_tokens`, `request_id`, `model`, `route`, `cached_calls` y `cost_usd` |
| `GET /metrics` | `X-API-Key` | Peticiones, `error_rate`, latencia p50/p95, tokens, SLO y `cost_usd` acumulado de este proceso |

Errores: `401 {"detail": "invalid or missing API key"}`; `429 {"detail": "rate limit exceeded"}` con
`Retry-After`; `422` si la pregunta está vacía o es demasiado larga; `502` si el modelo real falla;
`500 {"detail": "internal error", "request_id": ...}` ante cualquier otra falla. Toda respuesta lleva
`X-Request-ID`: si el cliente lo manda (1 a 128 caracteres de `A-Z a-z 0-9 . _ : -`) se respeta; si no, se genera
un UUID.

## Seguridad

- **Clave por cliente**: `API_KEYS=cliente:clave,cliente2:clave2`. La clave se compara con `hmac.compare_digest` y
  nunca se escribe en el log, la traza ni un error. Si `API_KEYS` está mal formada, el producto no arranca y el
  mensaje dice qué entrada falla y su largo, nunca su valor.
- **Límite por cliente**: más de `RATE_LIMIT_PER_MINUTE` preguntas en un minuto responden `429` con `Retry-After`.
  El límite es por proceso.
- **Auditoría**: el evento `audit` registra cada herramienta (`tool_call`) y cada rechazo (`auth_failed` con su
  motivo, `rate_limited` con la espera), con el `client` cuando se conoce.
- **Compliance como código**: con `ENVIRONMENT=production` el producto **no arranca** si `MODEL_PROVIDER=fake`, si
  `API_KEYS` está vacía o tiene una clave de menos de 16 caracteres, o si `LANGSMITH_TRACING=true` sin
  `HIDE_INPUTS` y `HIDE_OUTPUTS` en `true`. Detalle en [`doc/compliance-checklist.md`](doc/compliance-checklist.md).
- **Página**: la clave vive en `sessionStorage` y viaja en `X-API-Key`; con `401` muestra «Clave inválida» y con
  `429` «Demasiadas preguntas; espera N segundos».

## Costos

| Palanca | Cómo funciona | Variables |
| --- | --- | --- |
| 1. Enrutamiento | Regla en código: el modelo grande si `ROUTING=false`, si la pregunta pasa de `ROUTER_MAX_CHARS` caracteres o si contiene «compara», «resume», «explica», «por qué», «analiza» o «todos» (sin importar mayúsculas ni acentos); el pequeño en otro caso | `ROUTING`, `ROUTER_MAX_CHARS`, `MODEL_SMALL`, `MODEL_LARGE` |
| 2. Ventana | Al modelo solo llegan los últimos N mensajes del historial | `HISTORY_WINDOW` |
| 3. Tope de salida | `max_tokens` en cada llamada al modelo real | `MODEL_MAX_TOKENS` |
| 4. Caché | Se manda `cf-aig-cache-ttl`; si el gateway responde `cf-aig-cache-status: HIT`, la llamada no cuesta | `CACHE_TTL_SECONDS` |

`route` dice por qué se eligió el modelo: `routing_off`, `long_question`, `keyword:<palabra>` o `simple`.
`cost_usd` se calcula por llamada con los precios de `data/model_prices.json` (USD por millón de tokens, copia de
`spec/model_prices.json`; los precios cambian: revisa la fuente antes de estimar). Con el modelo simulado hay dos
modelos con los nombres de `MODEL_SMALL` y `MODEL_LARGE`: responden igual y cuestan según su nombre, así las
palancas se miden sin red.

## Variables de entorno

Todas están en [`.env.example`](.env.example), con los secretos vacíos.

| Variable | Por omisión | Para qué |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development` o `production` (aplica las reglas de compliance) |
| `API_KEYS` | (sin valor) | Clientes y claves, `cliente:clave,...`. **Secreto** |
| `RATE_LIMIT_PER_MINUTE` | `30` | Preguntas por cliente por minuto (entero >= 1) |
| `MODEL_PROVIDER` | `fake` | `fake` (simulado) o `gateway` (Workers AI vía AI Gateway) |
| `AGENT_MAX_STEPS` | `3` | Tope de vueltas del agente (entero >= 1) |
| `MAX_QUESTION_CHARS` | `2000` | Largo máximo de la pregunta (entero >= 1) |
| `EMPLOYEES_PATH` | `data/employees.json` | Datos; una ruta relativa se toma desde `product/` |
| `SERVICE_NAME` | `hr-assistant` | Campo `service` del log |
| `SLO_P95_MS` | `2000` | Meta del p95 de latencia en milisegundos (entero >= 1) |
| `LANGSMITH_TRACING` | `false` | `true` enciende las trazas, solo si además existe `LANGSMITH_API_KEY` |
| `LANGSMITH_API_KEY` | (sin valor) | Clave de LangSmith. **Secreto**: va en el entorno, nunca en el código ni en el log |
| `LANGSMITH_PROJECT` | (de LangSmith) | Proyecto donde caen las trazas (opcional) |
| `HIDE_INPUTS` / `HIDE_OUTPUTS` | `true` | No mandar a LangSmith el texto de preguntas ni respuestas |
| `CF_ACCOUNT_ID` | (sin valor) | Cuenta de Cloudflare; obligatoria con `gateway` |
| `CF_GATEWAY_ID` | `default` | Nombre del AI Gateway; obligatoria con `gateway` |
| `CF_API_TOKEN` | (sin valor) | Token de Workers AI. **Secreto**; obligatoria con `gateway` |
| `MODEL_SMALL` | `@cf/meta/llama-3.1-8b-instruct-fast` | Modelo barato (debe tener precio) |
| `MODEL_LARGE` | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | Modelo capaz (debe tener precio) |
| `ROUTING` | `true` | Enrutamiento por pregunta |
| `ROUTER_MAX_CHARS` | `80` | Preguntas más largas van al modelo capaz (entero >= 1) |
| `HISTORY_WINDOW` | `6` | Mensajes de historial que se mandan al modelo (entero >= 0) |
| `MODEL_MAX_TOKENS` | `300` | Tope de tokens de salida por llamada (entero >= 1) |
| `MODEL_TEMPERATURE` | `0` | Temperatura (0 a 2); con 0 las evaluaciones son repetibles |
| `MODEL_TIMEOUT_SECONDS` | `30` | Tiempo máximo de una llamada al modelo real (número > 0) |
| `CACHE_TTL_SECONDS` | `0` | Caché del gateway en segundos (0: apagada) |
| `PRICES_PATH` | `data/model_prices.json` | Precios por modelo; una ruta relativa se toma desde `product/` |
| `PORT` | `8000` | Puerto del contenedor (Render lo define) |

Un valor inválido (por ejemplo `AGENT_MAX_STEPS=0`, `HIDE_INPUTS=maybe` o `MODEL_PROVIDER=gateway` sin
`CF_API_TOKEN`) o una configuración de producción que no cumple impiden arrancar, con un mensaje que dice qué
corregir y nunca muestra un secreto.

## Observabilidad

**Log**: una línea JSON por evento en la salida estándar, con `ts` (UTC), `service`, `event` y `request_id`.
Las letras con acento y otros caracteres no ASCII se escriben escapados (la «í» queda como una barra invertida
seguida de `u00ed`), así la línea es JSON válido en cualquier consola. Eventos:

- `request`: `client` (si se autenticó), `method`, `path`, `status`, `latency_ms`.
- `agent_run`: `client`, `question_chars`, `history_messages` (recibidos), `history_sent` (enviados al modelo),
  `steps`, `tools`, `input_tokens`, `output_tokens`, `stopped` (`answer` o `max_steps`), `model`, `route`,
  `cached_calls` y `cost_usd`. Nunca el texto de la pregunta ni de la respuesta.
- `audit`: `tool_call` (`client`, `tool`, `args`), `auth_failed` (`reason`: `missing` o `invalid`, `path`) y
  `rate_limited` (`client`, `retry_after`). Nunca la clave.
- `error`: `error_type` (sin mensaje ni traza); `ModelUnavailable` cuando falla el modelo real.

Para seguir una petición, filtra la salida del servidor por su Id de soporte:

```bash
uv run uvicorn app.main:app --no-access-log | grep demo-123              # Bash
uv run uvicorn app.main:app --no-access-log | Select-String demo-123     # PowerShell
```

**Métricas**: `GET /metrics` (con clave) cuenta el tráfico de usuarios de este proceso (no cuenta `/health` ni
`/metrics`). El p50 y el p95 salen de las últimas 1000 latencias; `error_rate` cuenta solo respuestas `5xx`
(incluido el `502` del modelo real); `slo.met` es `true` si el p95 está por debajo de `SLO_P95_MS`; `cost_usd`
suma el costo de todas las respuestas.

**Trazas**: con `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY`, cada corrida del agente, cada llamada al modelo y
cada herramienta quedan en LangSmith con `request_id`, `client`, `model` y `route` en sus metadatos. Con
`HIDE_INPUTS`/`HIDE_OUTPUTS` en `true` no se manda el texto. Sin la clave, el producto funciona igual y no se
conecta.

## Despliegue

Nada se publica sin que una persona lo apruebe. Las piezas:

- **Contenedor** (`Dockerfile`): `python:3.12-slim`, usuario sin privilegios, solo `app/` y `data/`, escucha en
  `${PORT:-8000}` con `--no-access-log` y tiene `HEALTHCHECK`. `.dockerignore` deja fuera `.env`, las pruebas y los
  cachés.
- **CI** (`.github/workflows/ci.yml`): en cada push y pull request, cuatro etapas encadenadas que se ven en la
  pestaña Actions: `style` (pylint) → `unit` (pruebas, cobertura >= 80 %) → `e2e` (Playwright con el modelo
  simulado) → `docker` (construye la imagen).
- **Render** (`render.yaml`): Blueprint con el servicio `m4-hr-assistant` (el mismo nombre del repositorio),
  `runtime: docker`, `plan: free`, `healthCheckPath: /health` y `autoDeployTrigger: checksPass` (publica solo si
  el CI pasa). Declara `ENVIRONMENT=production`, `MODEL_PROVIDER=gateway` y `HIDE_*=true`.

Para publicar, una vez aprobado:

1. Sube el repositorio a GitHub y revisa en **Actions** que las cuatro etapas pasen.
2. En Render: **New > Blueprint**, elige el repositorio. El resumen debe decir **Create web service** (si dice
   Update, ya existe un servicio con ese nombre).
3. Escribe en Render los secretos `API_KEYS`, `CF_ACCOUNT_ID` y `CF_API_TOKEN` (nunca en el repositorio ni en un
   chat). `LANGSMITH_API_KEY` se agrega en **Environment** solo si enciendes las trazas.
4. Cuando el servicio esté **Live**, corre la prueba de humo con la clave de un cliente (solo la parte después de
   `cliente:`) en una variable de entorno:

```bash
SMOKE_API_KEY='<clave>' python scripts/smoke.py https://m4-hr-assistant.onrender.com          # Bash
```

```powershell
$env:SMOKE_API_KEY = '<clave>'; python scripts/smoke.py https://m4-hr-assistant.onrender.com   # PowerShell
```

Imprime `[OK]` o `[FAIL]` para `/health` (200), `/ask` sin clave (401) y `/ask` con clave (200). El plan gratuito
duerme el servicio tras 15 minutos sin uso; la primera petición puede tardar hasta un minuto.

## Pruebas

Desde la raíz de la fábrica:

```bash
uv run python -m verification unit       # pruebas del producto, cobertura >= 80 %
uv run python -m verification contract   # contrato contra el producto corriendo
uv run python -m verification qa         # pylint, 88 columnas, sin emojis, documentos en su lugar
uv run python -m verification e2e        # Playwright: la página como la usa una persona (e2e/)
uv run python -m verification evaluate   # calidad y costo: línea base contra palancas
```

Las pruebas nunca salen a la red: el modelo real se prueba con un cliente HTTP falso y usan claves de prueba que
no sirven en ningún otro lugar. Para `e2e` hace falta Chromium de Playwright (`uv run playwright install chromium`;
detrás de un proxy que inspecciona TLS, con `NODE_USE_SYSTEM_CA=1` para usar los certificados del sistema).
