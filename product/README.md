# HR Assistant

Asistente de Recursos Humanos que responde preguntas sobre empleados desde una página web. Es una API FastAPI
**sin estado** con un agente de bucle con tope y dos herramientas de solo lectura (`get_employee`,
`search_by_department`) sobre `data/employees.json`. Cada petición se puede rastrear y medir: `request_id`, log
JSON, auditoría de herramientas, `/metrics` con SLO y trazas opcionales en LangSmith.

Arquitectura y decisiones: [`doc/architecture.md`](doc/architecture.md) y [`doc/adr/`](doc/adr/).

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/).
- Las dependencias de `requirements.txt` (en la fábrica, el mismo entorno de la raíz).

## Cómo correrlo

Desde esta carpeta (`product/`):

```bash
uv run uvicorn app.main:app
```

Abre http://127.0.0.1:8000 y pregunta, por ejemplo, «¿Quién es EMP-0004?» o «¿Quiénes trabajan en Finanzas?».
Cada respuesta muestra un **Id de soporte** (el `request_id`).

## API

| Método y ruta | Qué hace |
| --- | --- |
| `GET /` | La página web |
| `GET /health` | `{"status": "ok", "pid": ...}` |
| `POST /ask` | Cuerpo `{"question": "...", "history": [...]}`; responde `answer`, `steps`, `tools_used`, `input_tokens`, `output_tokens` y `request_id` |
| `GET /metrics` | Peticiones, `error_rate`, latencia p50/p95, tokens y SLO de este proceso |
| `GET /docs` | Documentación interactiva (Swagger) |

Toda respuesta lleva el encabezado `X-Request-ID`. Si el cliente lo manda (1 a 128 caracteres de
`A-Z a-z 0-9 . _ : -`), se respeta; si no, se genera un UUID. Un error interno responde
`500 {"detail": "internal error", "request_id": ...}`.

## Variables de entorno

| Variable | Por omisión | Para qué |
| --- | --- | --- |
| `MODEL_PROVIDER` | `fake` | Modelo del agente; por ahora solo el simulado |
| `AGENT_MAX_STEPS` | `3` | Tope de vueltas del agente (entero >= 1) |
| `MAX_QUESTION_CHARS` | `2000` | Largo máximo de la pregunta (entero >= 1) |
| `EMPLOYEES_PATH` | `data/employees.json` | Datos; una ruta relativa se toma desde `product/` |
| `SERVICE_NAME` | `hr-assistant` | Campo `service` del log |
| `SLO_P95_MS` | `2000` | Meta del p95 de latencia en milisegundos (entero >= 1) |
| `LANGSMITH_TRACING` | `false` | `true` enciende las trazas, solo si además existe `LANGSMITH_API_KEY` |
| `LANGSMITH_API_KEY` | (sin valor) | Clave de LangSmith. **Secreto**: va en el entorno, nunca en el código ni en el log |
| `LANGSMITH_PROJECT` | (de LangSmith) | Proyecto donde caen las trazas (opcional) |
| `HIDE_INPUTS` / `HIDE_OUTPUTS` | `true` | No mandar a LangSmith el texto de preguntas ni respuestas |

Un valor inválido (por ejemplo `AGENT_MAX_STEPS=0` o `HIDE_INPUTS=maybe`) impide arrancar, con un mensaje que dice
qué variable corregir.

## Observabilidad

**Log**: una línea JSON por evento en la salida estándar, con `ts` (UTC), `service`, `event` y `request_id`.
Los caracteres no ASCII se escapan (`í`). Eventos:

- `request`: `method`, `path`, `status`, `latency_ms`.
- `agent_run`: `question_chars`, `history_messages`, `steps`, `tools`, `input_tokens`, `output_tokens` y
  `stopped` (`answer` o `max_steps`). Nunca el texto de la pregunta ni de la respuesta.
- `audit`: `action: "tool_call"`, `tool` y `args`, uno por herramienta ejecutada.
- `error`: `error_type` (sin mensaje ni traza).

Para seguir una petición, filtra la salida del servidor por su Id de soporte:

```bash
uv run uvicorn app.main:app | grep demo-123                  # Bash
uv run uvicorn app.main:app | Select-String demo-123         # PowerShell
```

**Métricas**: `GET /metrics` cuenta el tráfico de usuarios de este proceso (no cuenta `/health` ni `/metrics`).
El p50 y el p95 salen de las últimas 1000 latencias; `error_rate` cuenta solo respuestas `5xx`;
`slo.met` es `true` si el p95 está por debajo de `SLO_P95_MS`.

**Trazas**: con `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY`, cada corrida del agente, cada llamada al modelo y
cada herramienta quedan en LangSmith con el `request_id` en sus metadatos. Con `HIDE_INPUTS`/`HIDE_OUTPUTS` en
`true` no se manda el texto. Sin la clave, el producto funciona igual y no se conecta.

## Pruebas

Desde la raíz de la fábrica:

```bash
uv run python -m verification unit       # pruebas del producto, cobertura >= 80 %
uv run python -m verification contract   # contrato contra el producto corriendo
uv run python -m verification qa         # pylint, 88 columnas, sin emojis, documentos en su lugar
```
