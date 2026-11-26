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

## Actualización (2026-11-26): «Authenticated Gateway» apagado y la caché

- El gateway `default` de la cuenta tenía activada su propia autenticación y respondía `401` (código 2009) a las
  llamadas con el token de Workers AI; Workers AI directo sí respondía. Decisión de la persona: apagar
  «Authenticated Gateway».
- Cada llamada sigue exigiendo el token de Workers AI (`Authorization: Bearer`): sin él, el proveedor la rechaza.
  Lo que se pierde es una segunda credencial, propia del gateway.
- La caché del gateway sigue **apagada** (`CACHE_TTL_SECONDS=0`). Con el gateway sin autenticación propia, una
  respuesta en caché podría servirse a quien repita la misma petición; con la caché apagada, cada respuesta sale
  del proveedor y exige el token.
- La palanca 4 (caché) se documenta y se mide en el análisis de costos, pero no se enciende en producción mientras
  el gateway no tenga autenticación propia.
- Reintentos, circuit breaker y fallback, que este ADR dejaba pendientes, están en el ADR 0018. Las llamadas
  llevan el `request_id` en `cf-aig-metadata` (ADR 0021).
