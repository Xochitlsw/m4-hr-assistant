# 0019 · Concurrencia, readiness, contrato de la API y separación de entornos

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción

## Contexto

El reto pide manejar varias peticiones a la vez, un health check, la API documentada en OpenAPI y una
separación clara entre desarrollo y producción. La API ya era sin estado (ADR 0001) y tenía `/health` y `/docs`,
pero:

- La concurrencia no tenía evidencia ni un modelo documentado.
- `/health` solo dice que el proceso vive, no si puede atender preguntas.
- El esquema OpenAPI no documentaba los errores, ni ejemplos, ni el contrato exportado.
- Desarrollo y producción compartían proyecto de LangSmith.

## Decisión

**Modelo de concurrencia.**

- Las rutas son `def`, no `async def`: FastAPI las corre en su pool de hilos, así que una llamada bloqueante al
  modelo real (`httpx.Client`) no detiene el event loop. El cliente HTTP es uno por proceso y se comparte entre
  hilos (crearlo cuesta más de un segundo en algunos equipos).
- Todo el estado compartido tiene candado: métricas, log, límite por cliente, claves fallidas, presupuesto y
  circuitos. El directorio y los precios son de solo lectura.
- Procesos: uvicorn toma `WEB_CONCURRENCY` (1 por omisión). En el plan gratuito de Render (512 MB) conviene 1.
  Cada proceso tiene sus propias métricas, límites y circuitos; por eso `/health` y `/metrics` dicen el `pid`.
  LangSmith y el log de la plataforma sí juntan todos los procesos. Compartir el estado entre procesos o réplicas
  (Redis) queda como mejora.
- Evidencia: `scripts/load_test.py`, con un solo `AsyncClient` y un semáforo, mide códigos, peticiones por segundo
  y p50/p95/máximo. Contra uvicorn local con el modelo simulado: 200 de 200 con 20 a la vez, sin errores y con
  el log íntegro (`doc/evidence/load-test.txt`). Con el límite por omisión, el `429` protege al servicio. Contra
  Render (2026-11-26, plan gratuito, modelo real): 20 de 20 con 5 a la vez, p50 de 609 ms y p95 de 3362 ms
  (`doc/evidence/load-test-render.txt`).

**`/health` y `/ready` son cosas distintas.**

- `/health` es la prueba de vida: barata, sin clave ni llamadas externas. Render la usa
  (`healthCheckPath`) y no cambia de contrato.
- `/ready` es la disponibilidad: `200` si el directorio está cargado y algún modelo no tiene el circuito abierto;
  `503` si no. Reporta cada comprobación y nunca llama al modelo, para no gastar cuota.
- La configuración y los precios ya se validaron al arrancar: si fallaran, la aplicación no estaría corriendo.

**Contrato OpenAPI** (`app/api_docs.py`):

- Versión 1.1.0, una descripción con autenticación, límites y privacidad, y etiquetas (`ask`, `operations`,
  `page`).
- Cada ruta tiene una descripción en inglés y modelos de respuesta (`HealthResponse`, `ReadyResponse`).
- `POST /ask` documenta `400`, `401`, `413`, `422`, `429`, `502` y `503`, cada uno con `ErrorBody` y un ejemplo; los
  `429` y `503`, con su `Retry-After`. El cuerpo y la respuesta llevan ejemplos.
- `X-API-Key` es un esquema de seguridad: Swagger muestra **Authorize**.
- `scripts/export_openapi.py` escribe `doc/openapi.json` en UTF-8. `tests/test_api_contract.py` falla si el
  archivo y la API se separan.

**Entornos.**

| | Desarrollo (tu equipo) | Producción (Render) |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `production`: aplica compliance (ADR 0011, 0014, 0016 y 0017) |
| Modelo | `fake` por omisión; `gateway` con tu `.env` | `gateway` (compliance no acepta `fake`) |
| Claves | Las de tu `.env` | `API_KEYS` en Render > Environment, de 16 caracteres o más |
| Trazas | Proyecto `m4-hr-assistant-dev` | Proyecto `m4-hr-assistant-prod` |
| Origen de las peticiones | Socket (`TRUSTED_PROXY_HOPS=0`) | Proxy de Render (`TRUSTED_PROXY_HOPS=1`) |
| HSTS | No | Sí |
| Despliegue | `uvicorn` a mano | Blueprint, solo con el CI en verde |

## Consecuencias

- Un balanceador u orquestador puede usar `/ready` para sacar una copia que no puede responder; Render sigue
  usando `/health`.
- Cambiar la API obliga a regenerar `doc/openapi.json`, y la prueba lo recuerda.
- Con varios workers, los límites por cliente son por proceso (más permisivos en total); está documentado.
