# 0007 · Enrutamiento por regla y ventana de historial

Fecha: 2026-11-25 · Estado: aceptada · Sesión 3

## Contexto

La mayoría de las preguntas del personal de RH son búsquedas simples («¿Quién es EMP-0004?»). Mandarlas al
modelo grande cuesta unas seis veces más por token sin mejorar la respuesta. Además, la API sin estado (ADR 0001)
recibe el historial completo en cada petición, y mandarlo entero al modelo hace crecer los tokens de entrada con
cada turno.

## Decisión

- Un `Router` en código, no otro modelo, elige el modelo por pregunta. Usa `MODEL_LARGE` si `ROUTING=false`, si
  la pregunta pasa de `ROUTER_MAX_CHARS` caracteres o si contiene «compara», «resume», «explica», «por qué»,
  «analiza» o «todos» (sin mayúsculas ni acentos). En otro caso usa `MODEL_SMALL`.
- La respuesta dice el motivo en `route` (`routing_off`, `long_question`, `keyword:<palabra>` o `simple`).
- Al modelo solo se mandan los últimos `HISTORY_WINDOW` mensajes del historial (0 = ninguno). La ventana se
  aplica en la API, antes del agente.
- Cada palanca se queda solo si `verification evaluate` muestra la misma calidad con menos costo que la línea base
  (`ROUTING=false`, sin ventana).

## Consecuencias

- La regla es gratis, determinista, explicable y fácil de probar; `route` permite auditar cada decisión.
- Una pregunta compleja sin palabra clave puede ir al modelo pequeño; las palabras se buscan como texto contenido
  para que los errores caigan del lado caro y no del lado de la calidad.
- Con la ventana, el asistente puede olvidar algo dicho hace más de `HISTORY_WINDOW` mensajes; el valor se ajusta
  midiendo.
- El costo por petición queda acotado por el tamaño de la ventana, no por lo larga que sea la conversación.
