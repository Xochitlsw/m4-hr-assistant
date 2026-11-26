# 0008 · Modelo real por AI Gateway, caché y costo por llamada

Fecha: 2026-11-25 · Estado: aceptada · Sesión 3

## Contexto

El producto necesita un modelo real (Cloudflare Workers AI a través de AI Gateway) sin perder lo que da el
modelo simulado: pruebas deterministas, sin red y sin cuota. Además, cada respuesta debe decir cuánto costó, y las
palancas de costo se deben poder medir con los dos modelos.

## Decisión

- `GatewayModel` implementa la interfaz `complete(messages)` (ADR 0002). Llama al endpoint compatible con OpenAI
  del gateway con un `httpx.Client` **inyectado**, uno por proceso, con timeout (`MODEL_TIMEOUT_SECONDS`). Manda
  `MODEL_MAX_TOKENS` y `MODEL_TEMPERATURE` en cada llamada.
- Un adaptador tolerante convierte la salida del modelo real al protocolo: quita los bloques de código, toma el
  primer objeto JSON y, si no hay ninguno, entrega `{"answer": <texto>}`. El agente sigue validando el protocolo y
  la lista blanca de herramientas.
- Cualquier falla (HTTP, timeout, formato) es `ModelUnavailable`, que la API responde con
  `502 {"detail": "model unavailable", "request_id"}`.
- Con `CACHE_TTL_SECONDS` > 0 se manda `cf-aig-cache-ttl`; una respuesta con `cf-aig-cache-status: HIT` es una
  llamada cacheada.
- `PricedModel` envuelve cualquier modelo (simulado o real) y calcula el costo de cada llamada con los precios de
  `data/model_prices.json` según el nombre del modelo; una llamada cacheada cuesta 0. El agente suma costo y
  llamadas cacheadas. Con `fake` hay dos modelos simulados con los nombres de `MODEL_SMALL` y `MODEL_LARGE`.
- Al arrancar, los dos modelos deben tener precio; si no, `ConfigError`.

## Consecuencias

- El agente y el enrutador no saben qué modelo hay detrás; cambiar de proveedor es otro adaptador.
- Las palancas se miden sin red con el modelo simulado y los precios reales; el costo con el modelo real se
  confirma con una muestra pequeña, por la cuota diaria.
- El costo es una estimación con precios publicados: hay que revisar `model_prices.json` cuando cambien.
- El adaptador tolerante puede convertir en respuesta un texto que debió ser una herramienta; la calidad real se
  mide con la muestra del modelo real.
- Sin reintentos ni circuit breaker todavía: una falla del proveedor llega a la persona como un `502` claro.
