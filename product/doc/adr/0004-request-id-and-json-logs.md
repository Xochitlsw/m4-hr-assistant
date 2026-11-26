# 0004 · `request_id` y log JSON por evento

Fecha: 2026-11-25 · Estado: aceptada · Sesión 2

## Contexto

Con varias copias sin estado y peticiones concurrentes, «¿qué pasó con la petición X?» solo se contesta si cada
línea del log se puede ligar a una petición. El log no debe guardar lo que pregunta el personal de RH ni lo que
responde el asistente, porque contiene datos personales de empleados.

## Decisión

- Cada petición tiene un `request_id`. Se usa el `X-Request-ID` del cliente si tiene de 1 a 128 caracteres de
  `[A-Za-z0-9._:-]`; si no lo manda o no cumple, se genera un UUID4. Va en el encabezado `X-Request-ID` de toda
  respuesta (también `422` y `500`) y en el cuerpo de `POST /ask`.
- El log va a `stdout`, una línea JSON por evento, con `ts` (ISO 8601 UTC, de un reloj inyectado), `service`,
  `event` y `request_id`. Eventos: `request`, `agent_run`, `audit` (`tool_call`) y `error`.
- `agent_run` lleva solo conteos (`question_chars`, `history_messages`, `steps`, `tools`, tokens) y `stopped`;
  nunca el texto. `error` lleva solo `error_type`; la respuesta es un `500` genérico con `request_id`.
- Un middleware de petición fija el `request_id`, mide la latencia y escribe `request`. El agente avisa de cada
  herramienta ejecutada con un callback y no sabe de logs.

## Consecuencias

- Soporte busca el «Id de soporte» de la página en el log y ve `audit`, `agent_run` y `request` de esa petición.
- La plataforma (Render) recoge `stdout` sin configurar archivos ni agentes de logs.
- No se puede reconstruir la conversación desde el log; para depurar contenido hacen falta trazas con permiso
  explícito (ADR 0006).
- Un `X-Request-ID` raro del cliente se reemplaza, así que ese cliente pierde su correlación; se prefiere eso a
  aceptar texto arbitrario.
