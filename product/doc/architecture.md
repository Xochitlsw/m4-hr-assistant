# Arquitectura del HR Assistant · Sesión 4

Asistente de Recursos Humanos que responde preguntas sobre empleados desde una página web. Es una API FastAPI
**sin estado** con **un agente** de bucle con tope y dos herramientas de solo lectura:

- **Rastreable y medible** (sesión 2): cada petición deja su rastro.
- **Con costo visible** (sesión 3): puede usar un **modelo real** (Cloudflare Workers AI vía AI Gateway) y cada
  respuesta dice qué modelo usó y cuánto costó.
- **Lista para producción** (sesión 4): acceso con **clave por cliente**, **límite de peticiones**, **reglas de
  compliance** que impiden arrancar mal configurado, un **contenedor**, un **pipeline de CI** visible y un
  **Blueprint de Render** que publica solo si el pipeline pasa.

Decisiones:

- [ADR 0001](adr/0001-stateless-api.md): API sin estado.
- [ADR 0002](adr/0002-model-interface-and-json-protocol.md): interfaz del modelo, protocolo JSON y bucle con tope.
- [ADR 0003](adr/0003-dependency-injection-and-startup-config.md): inyección de dependencias y configuración.
- [ADR 0004](adr/0004-request-id-and-json-logs.md): `request_id` y log JSON por evento.
- [ADR 0005](adr/0005-process-metrics-and-slo.md): métricas por proceso y SLO.
- [ADR 0006](adr/0006-optional-langsmith-tracing.md): trazas opcionales en LangSmith.
- [ADR 0007](adr/0007-rule-based-routing-and-history-window.md): enrutamiento por regla y ventana de historial.
- [ADR 0008](adr/0008-gateway-model-cache-and-cost-accounting.md): modelo real, caché y costo por llamada.
- [ADR 0009](adr/0009-secrets-and-env-example.md): secretos solo en el entorno y `.env.example`.
- [ADR 0010](adr/0010-api-keys-and-rate-limit.md): clave por cliente y límite de peticiones.
- [ADR 0011](adr/0011-compliance-as-code.md): compliance como código.
- [ADR 0012](adr/0012-container-ci-and-render.md): contenedor, CI encadenado y Blueprint de Render.

## Componentes

```mermaid
flowchart LR
  user(["Personal de RH"]) --> page["Página web<br/>static/index.html · clave en sessionStorage"]
  page -- "POST /ask + X-API-Key" --> mw["Middleware de petición<br/>request_id · latencia · 500"]
  ops(["Soporte / plataforma"]) -- "GET /health · GET /metrics" --> mw
  mw --> auth["require_client · limit_rate<br/>main.py + security.py · 401 / 429"]
  auth --> api["Rutas<br/>main.py · AskService"]
  api -- "pregunta" --> router["Router<br/>routing.py · palancas 1 y 2"]
  api --> agent["Agente<br/>agent.py · bucle con tope"]
  catalog["catalog.py<br/>arma los dos modelos"] -. "al arrancar" .-> priced
  agent -- "complete(messages)" --> priced["PricedModel<br/>pricing.py · costo por llamada"]
  priced --> fake["FakeModel x2<br/>MODEL_PROVIDER=fake"]
  priced --> gw["GatewayModel x2<br/>gateway.py · palancas 3 y 4"]
  gw -- "httpx.Client inyectado" --> cf[("Cloudflare AI Gateway<br/>Workers AI")]
  agent -- "get_employee<br/>search_by_department" --> dir["Directorio<br/>directory.py (solo lectura)"]
  dir --> data[("data/employees.json")]
  mw -- "request · error" --> logs["EventLogger<br/>logs.py"]
  auth -- "audit: auth_failed · rate_limited" --> logs
  api -- "audit: tool_call · agent_run" --> logs
  logs --> stdout[/"stdout: una línea JSON por evento"/]
  mw -- "latencia · status" --> metrics["Metrics<br/>metrics.py"]
  api -- "tokens · costo" --> metrics
  agent -. "trazas si hay bandera y clave" .-> tracing["tracing.py"]
  tracing -.-> langsmith[("LangSmith")]
  config["config.py + compliance.py<br/>valida y aplica reglas de producción"] -. "al arrancar" .-> api
```

| Módulo | Responsabilidad |
| --- | --- |
| `app/main.py` | `create_app()` con dependencias inyectadas; middleware; autenticación y límite como dependencias; rutas `/`, `/health`, `/ask` y `/metrics`; `AskService`; `502` si el modelo falla |
| `app/security.py` | `KeyStore` (cliente de una clave con `hmac.compare_digest`) y `RateLimiter` (ventana deslizante por cliente) |
| `app/compliance.py` | Reglas de producción: con `ENVIRONMENT=production` la aplicación no arranca si alguna falla |
| `app/routing.py` | Regla de enrutamiento (modelo y `route`) y ventana de historial |
| `app/schemas.py` | Modelos Pydantic de la API: historial, cuerpo de `/ask` y `AskResponse` |
| `app/agent.py` | Bucle con tope, protocolo JSON, lista blanca, consumo (`Usage`) y aviso por herramienta |
| `app/catalog.py` | Arma el modelo pequeño y el grande (simulados o reales) envueltos en `PricedModel`, y valida sus precios |
| `app/llm.py` | Interfaz `complete(messages)`, `Completion` y `FakeModel` |
| `app/gateway.py` | Workers AI vía AI Gateway: petición, tope de salida, caché, `usage`, adaptador al protocolo y `ModelUnavailable` |
| `app/pricing.py` | Precios por modelo y `PricedModel` |
| `app/directory.py` | Carga y consulta de solo lectura de los empleados |
| `app/config.py` | Configuración desde el entorno por grupos, con valores por omisión y validación al arrancar |
| `app/clock.py` | Reloj inyectable: hora UTC y tiempo monótono para latencias y el límite |
| `app/logs.py` | Una línea JSON por evento, con candado entre hilos |
| `app/metrics.py` | Contadores del proceso, percentiles, tokens, costo y SLO, con candado entre hilos |
| `app/tracing.py` | Enciende o apaga LangSmith y envuelve corrida, modelo y herramientas |
| `app/static/index.html` | Página única; clave en `sessionStorage`; historial en el navegador; pinta con `textContent`; «Id de soporte» |

**Cambio frente al plan de la sesión 3**: el plan ponía `build_models()` en `llm.py`. Como `pricing.py` y
`gateway.py` importan `Completion` de `llm.py`, eso creaba una importación circular; `build_models()` vive en
`app/catalog.py`. En la sesión 4, `model_provider` pasa a `settings.models.provider` (ADR 0003).

## Flujo de una pregunta

```mermaid
sequenceDiagram
  participant P as Página
  participant W as Middleware
  participant S as Seguridad
  participant A as AskService
  participant G as Agente
  participant M as Modelo (con precio)
  participant L as Log (stdout)
  P->>W: POST /ask {question, history} + X-API-Key [X-Request-ID]
  W->>W: request_id y hora de inicio
  W->>S: require_client(X-API-Key)
  alt sin clave o clave inválida
    S->>L: audit auth_failed (sin la clave)
    S-->>P: 401 invalid or missing API key
  else cliente sobre su límite
    S->>L: audit rate_limited
    S-->>P: 429 rate limit exceeded + Retry-After
  else permitido
    S->>A: cliente
    A->>A: valida (422), enruta, recorta historial
    A->>G: run(question, history) con el modelo elegido
    G->>M: complete(messages) hasta AGENT_MAX_STEPS
    G->>L: audit tool_call (client, tool, args)
    A->>L: agent_run (client, conteos, model, route, cost_usd; sin texto)
    A-->>W: AskResponse (answer, ..., request_id, model, route, cached_calls, cost_usd)
  end
  W->>L: request (client, method, path, status, latency_ms)
  W-->>P: respuesta + X-Request-ID
```

Si el modelo real falla, la respuesta es `502 {"detail": "model unavailable", "request_id": ...}`; cualquier otra
falla, `500` genérico. Al arrancar, `load_settings()` valida los valores (incluida la forma de `API_KEYS`) y
`compliance` aplica las reglas de producción.

## Despliegue

```mermaid
flowchart LR
  dev(["Persona"]) -- "git push (tras aprobar)" --> gh[("GitHub")]
  gh --> ci["GitHub Actions<br/>style → unit → e2e → docker"]
  ci -- "checks en verde" --> render["Render<br/>Blueprint · runtime docker · plan free"]
  render -- "healthCheckPath /health" --> svc["Servicio web<br/>contenedor sin root"]
  secrets[/"Render > Environment<br/>API_KEYS · CF_ACCOUNT_ID · CF_API_TOKEN"/] -.-> svc
  smoke["scripts/smoke.py<br/>SMOKE_API_KEY"] -- "/health · /ask sin y con clave" --> svc
```

| Pieza | Archivo | Qué garantiza |
| --- | --- | --- |
| Contenedor | `Dockerfile`, `.dockerignore` | `python:3.12-slim`, usuario sin privilegios, solo `app/` y `data/`, `--no-access-log`, `HEALTHCHECK` |
| Pipeline | `.github/workflows/ci.yml` | Cuatro etapas encadenadas en cada push y pull request |
| Publicación | `render.yaml` | Servicio con el nombre del repositorio; despliega solo con checks en verde; secretos con `sync: false` |
| Prueba de humo | `scripts/smoke.py` | `/health` 200, `/ask` sin clave 401, `/ask` con clave 200 |
| Reglas | `doc/compliance-checklist.md` | Cada regla de producción, dónde está en el código y cómo se comprueba |

El CI y el Blueprint están dentro de `product/`, como si `product/` fuera la raíz de su propio repositorio. En un
repositorio donde `product/` es una subcarpeta, el CI va en `.github/workflows/` de la raíz y el Blueprint indica
`rootDir: product`.

## Costos

| Palanca | Cómo funciona | Configuración |
| --- | --- | --- |
| 1. Enrutamiento | Regla en código: grande si `ROUTING=false`, pregunta larga o palabra clave; si no, pequeño | `ROUTING`, `ROUTER_MAX_CHARS`, `MODEL_SMALL`, `MODEL_LARGE` |
| 2. Ventana | Solo los últimos N mensajes del historial llegan al modelo | `HISTORY_WINDOW` |
| 3. Tope de salida | `max_tokens` en cada llamada al modelo real | `MODEL_MAX_TOKENS` |
| 4. Caché | `cf-aig-cache-ttl`; un `HIT` del gateway cuesta 0 | `CACHE_TTL_SECONDS` |

`cost_usd` = (tokens de entrada × precio de entrada + tokens de salida × precio de salida) / 1 000 000, por
llamada y según el nombre del modelo (`data/model_prices.json`). `verification evaluate` compara la línea base
(`ROUTING=false`, sin ventana) con la configuración por omisión: pasa solo si la calidad no baja y el costo sí baja.
El límite por cliente (ADR 0010) también acota el gasto.

## Observabilidad

| Pregunta | Dónde se contesta |
| --- | --- |
| ¿Qué pasó con la petición X? | Log: `grep` del `request_id` (el «Id de soporte» de la página) → `audit`, `agent_run`, `request`, `error` |
| ¿Quién preguntó? | Campo `client` en `request`, `agent_run`, `audit` y en los metadatos de la traza |
| ¿Quién intentó entrar sin permiso o abusó? | `audit` con `auth_failed` y `rate_limited` |
| ¿Qué herramientas usó el agente y con qué argumentos? | `audit` con `tool_call` |
| ¿Qué modelo atendió la pregunta, por qué y cuánto costó? | Respuesta de `/ask` y evento `agent_run` |
| ¿Está lento o fallando este proceso? | `GET /metrics` (con clave): `requests`, `error_rate`, p50/p95, `slo.met` |
| ¿Cuánto se ha gastado? | `GET /metrics`: `tokens` y `cost_usd` acumulados |
| ¿Cómo razonó el agente paso a paso? | LangSmith (si está encendido), por los metadatos `request_id`, `client`, `model` y `route` |

## Configuración

| Variable | Por omisión | Regla |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development` o `production` (aplica las reglas de compliance) |
| `API_KEYS` | (vacía) | Secreto; `cliente:clave,...`; en producción, claves de 16 caracteres o más |
| `RATE_LIMIT_PER_MINUTE` | `30` | Entero >= 1; peticiones a `/ask` por cliente por minuto |
| `MODEL_PROVIDER` | `fake` | `fake` o `gateway`; en producción, `gateway` |
| `AGENT_MAX_STEPS` | `3` | Entero >= 1 |
| `MAX_QUESTION_CHARS` | `2000` | Entero >= 1 |
| `EMPLOYEES_PATH` | `data/employees.json` | Relativa a `product/`; el archivo debe existir y ser JSON válido |
| `SERVICE_NAME` | `hr-assistant` | Campo `service` del log |
| `SLO_P95_MS` | `2000` | Entero >= 1; meta del p95 en milisegundos |
| `LANGSMITH_TRACING` | `false` | `true` o `false`; enciende las trazas solo si además existe la clave |
| `HIDE_INPUTS` / `HIDE_OUTPUTS` | `true` | `true` o `false`; en producción con trazas, `true` |
| `LANGSMITH_API_KEY` | (sin valor) | Secreto; lo lee el cliente de LangSmith; nunca se registra |
| `CF_ACCOUNT_ID` | (vacía) | Obligatoria con `gateway` |
| `CF_GATEWAY_ID` | `default` | Obligatoria con `gateway` |
| `CF_API_TOKEN` | (vacía) | Secreto; obligatoria con `gateway`; nunca se registra |
| `MODEL_SMALL` | `@cf/meta/llama-3.1-8b-instruct-fast` | Debe tener precio |
| `MODEL_LARGE` | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | Debe tener precio |
| `ROUTING` | `true` | `true` o `false` |
| `ROUTER_MAX_CHARS` | `80` | Entero >= 1 |
| `HISTORY_WINDOW` | `6` | Entero >= 0 |
| `MODEL_MAX_TOKENS` | `300` | Entero >= 1 |
| `MODEL_TEMPERATURE` | `0` | Número entre 0 y 2 |
| `MODEL_TIMEOUT_SECONDS` | `30` | Número > 0 |
| `CACHE_TTL_SECONDS` | `0` | Entero >= 0; 0 apaga la caché |
| `PRICES_PATH` | `data/model_prices.json` | Relativa a `product/`; debe tener los dos modelos |
| `PORT` | `8000` | Puerto del contenedor (Render lo define) |

Un valor inválido o una configuración de producción que no cumple impiden arrancar, con un mensaje que nunca
incluye un secreto. `product/.env.example` lista todas las variables con los secretos vacíos.

## Propiedades

- **Sin estado**: escalar es correr más copias; el `pid` de `/health` y `/metrics` muestra qué copia contestó (el
  límite de peticiones es por copia).
- **Acotado**: como máximo `AGENT_MAX_STEPS` llamadas al modelo por pregunta, cada una con tope de salida y
  timeout, una ventana de historial fija y un límite de peticiones por cliente.
- **Mínima agencia**: dos herramientas de solo lectura, validadas antes de ejecutarse y auditadas.
- **Acceso controlado**: clave por cliente comparada en tiempo constante; la clave nunca se registra.
- **Rastreable sin exponer datos**: el log y las trazas por omisión no llevan el texto de preguntas ni respuestas.
- **Costo visible**: cada respuesta y `/metrics` dicen cuánto costó.
- **Seguro al desplegar**: producción no arranca si incumple una regla; Render publica solo con el CI en verde.
- **Seguro en la página**: las respuestas se pintan con `textContent`; la clave vive en `sessionStorage`.

## Cómo se comprueba

Desde la raíz de la fábrica:

- `uv run python -m verification unit`: pruebas del producto con cobertura de 80 % o más.
- `uv run python -m verification contract`: contrato de las sesiones 1 a 4 contra el producto corriendo.
- `uv run python -m verification qa`: pylint, 88 columnas, sin emojis y documentos en su lugar.
- `uv run python -m verification e2e`: pruebas de Playwright de la página, con clave.
- `uv run python -m verification evaluate`: calidad y costo, línea base contra palancas.
- `uv run python -m verification production <url>`: prueba de humo contra Render, con `SMOKE_API_KEY` (después de
  publicar).
