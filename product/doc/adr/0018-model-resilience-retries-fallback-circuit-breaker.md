# 0018 · Resiliencia del modelo real: timeouts, reintentos, fallback y circuit breaker

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción · Completa el ADR 0008

## Contexto

El modelo real (Workers AI a través de AI Gateway) puede fallar por razones pasajeras: un `5xx`, un `429` por
cuota o carga, un corte de red. Hasta ahora cualquier falla era un `502` inmediato (ADR 0008), con un solo
timeout de 30 s para todo. Una caída larga del modelo grande hacía fallar las preguntas complejas aunque el
pequeño funcionara, y cada petición seguía esperando un timeout contra un servicio caído.

## Decisión

**Cada falla del gateway dice si es transitoria** (`app/gateway.py`):

- Error de red, timeout, `429` o `5xx`: transitoria.
- Otro `4xx` (por ejemplo, un token inválido) o una respuesta inútil: no lo es.
- Si el gateway manda `Retry-After`, la falla lo lleva.

**Timeouts.** Conectar tiene su propio tope corto (`MODEL_CONNECT_TIMEOUT_SECONDS`, 5 s): si el gateway no
acepta la conexión, se sabe pronto. La llamada completa sigue con `MODEL_TIMEOUT_SECONDS` (30 s).

**Reintentos** (`app/resilience.py` · `Retrier`):

- Hasta `MODEL_MAX_RETRIES` (2), solo para fallas transitorias.
- Espera `MODEL_RETRY_BACKOFF_SECONDS × 2^(n-1)` (0.5 s y 1 s) con jitter entre 0.5 y 1.5, para que varias
  copias no reintenten al mismo tiempo.
- Si hay `Retry-After`, espera eso, pero solo hasta 10 s: más, y no reintenta, porque la persona está esperando.
- No reintenta si ya pasó `MODEL_TIMEOUT_SECONDS` desde el primer intento. Un timeout no se repite: ya gastó el
  tiempo de la llamada.

**Circuit breaker por modelo:**

- Después de `CIRCUIT_FAILURE_THRESHOLD` llamadas fallidas seguidas (5, ya con sus reintentos), el circuito se
  abre `CIRCUIT_OPEN_SECONDS` (30 s).
- Mientras está abierto, cada llamada falla al instante con `ModelCircuitOpen`. La API responde
  `503 {"detail": "model temporarily unavailable", "request_id": ...}` con `Retry-After`.
- Después deja pasar **una** llamada de prueba: si responde, se cierra; si no, se vuelve a abrir.
- Cada cambio de estado deja una línea `model_circuit` en el log, con el modelo y el estado.

**Fallback del modelo grande al pequeño** (`AskService`):

- Si falla el modelo grande, incluso con el circuito abierto, la pregunta se responde con el pequeño.
- La respuesta lo dice: `model` es el pequeño y `route` termina en `+fallback` (por ejemplo,
  `keyword:compara+fallback`). El log registra `model_fallback`.
- Si el pequeño también falla, la respuesta es `502` o `503`.

**Visible en la operación.** `/ready` reporta el circuito de cada modelo y responde `503` si todos están abiertos
(ADR 0019). `agent_run` cuenta los reintentos (`model_retries`).

Todo usa el reloj, `sleep` y el azar inyectados, y `httpx.MockTransport`: las pruebas no esperan ni salen a la
red (`tests/test_resilience.py`, `tests/test_availability.py`).

## Consecuencias

- Una falla pasajera ya no llega a la persona: en el peor caso tarda unos 1.5 s más (dos reintentos).
- Una caída del modelo grande degrada la calidad de las preguntas complejas, pero el servicio sigue respondiendo.
  La métrica de rutas (O4) y el log `model_fallback` lo hacen visible.
- Con el circuito abierto se deja de golpear un servicio caído y no se gasta cuota.
- Los reintentos pueden costar: un `5xx` del gateway normalmente no cobra tokens, pero un timeout tras empezar a
  generar sí podría cobrarlos. Por eso los timeouts no se reintentan.
- El estado del circuito vive en cada proceso, como las métricas y los límites (ADR 0001, 0005 y 0019).

## Alternativas descartadas

- **Reintentar también los timeouts**: duplicaría la espera de la persona (60 s o más) y el costo posible.
- **Una librería** (tenacity, pybreaker): resuelve lo mismo, pero agrega dependencias; la lógica cabe en un módulo
  probado.
- **Fallback del pequeño al grande**: el grande cuesta unas 6 veces más; si el pequeño falla, se prefiere un
  `502` claro.
