# 0016 · Prompt injection, auditoría completa, cabeceras de seguridad y topes de producción

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción

## Contexto

- **Inyección.** El historial lo manda el cliente. Un turno `assistant` falsificado, o una pregunta que pide
  ignorar las reglas o revelar el prompt, llegaban al modelo sin revisión. Las capas de los ADR 0002 y 0013
  limitan el daño (dos herramientas de solo lectura en lista blanca, argumentos validados, tope de pasos, salida
  limpia), pero no detectaban el intento ni lo dejaban registrado.
- **Auditoría.** Se registraban herramientas, rechazos de clave y de límite, respuestas inválidas del modelo,
  limpieza de la salida y datos personales. Faltaban las peticiones rechazadas por tamaño o validación, el
  arranque (con qué configuración corre producción) y los arranques negados por compliance.
- **HTTP.** Las respuestas no traían cabeceras de seguridad. La página confía en `textContent`, pero nada impedía
  que corriera un script inyectado, que la API quedara en cachés o que otra página la metiera en un marco.
- **Topes de producción.** Compliance (ADR 0011) exigía modelo real, claves largas y trazas protegidas, pero un
  `MAX_BODY_BYTES` o un `AGENT_MAX_STEPS` enormes en producción desactivaban en silencio los topes de entrada,
  agencia y consumo.

## Decisión

**Inyección: heurísticas con nombre que bloquean (`app/injection.py`).**

- Siete reglas en español e inglés: `override` (ignorar u olvidar instrucciones), `system_prompt`, `role_switch`
  («actúa como», «you are now»), `reveal` (revelar prompt, claves, contraseñas o variables de entorno), `protocol`
  (alguien escribe el protocolo JSON del agente: `"tool"`, `"answer"`, `"args"`), `fake_role` (`SYSTEM:`,
  `<|im_start|>`, `[INST]`) y `jailbreak`.
- Revisan la pregunta y **cada** mensaje del historial, de los dos roles.
- Miran una copia sin caracteres invisibles, en NFKC, sin acentos y en minúsculas. Así un espacio de ancho cero,
  las letras de ancho completo o «IGNÓRA» no las esquivan.
- Una coincidencia responde `400 {"detail": "request looks like a prompt injection; ask about employees"}`.
  El modelo no recibe nada y se audita `injection_blocked` con el lugar y los nombres de las reglas, nunca el
  texto.
- La página explica en español que la pregunta parece una instrucción para el asistente.
- Orden en la entrada: inyección, después secretos (ADR 0014), después redacción de datos personales.

**Auditoría completa.** Todas son líneas `event: "audit"` con `action`, `request_id`, `client` cuando se conoce,
y nunca texto, claves ni valores:

| `action` | Cuándo | Campos |
| --- | --- | --- |
| `startup` | Arranca el servidor (ciclo de vida de FastAPI) | Entorno, proveedor y modelos, nombres de los clientes (sin claves), límite, topes, pasos, trazas y su privacidad |
| `startup_refused` | Compliance impide arrancar | Entorno y reglas incumplidas |
| `auth_failed` | `401` | Motivo (`missing`, `invalid`) y ruta |
| `rate_limited` | `429` | Espera |
| `request_rejected` | `413` o `422` | Motivo (`body_too_large`, `invalid_input`) y ruta |
| `injection_blocked` | `400` por inyección | Lugar y reglas |
| `secret_blocked` | `400` por secreto | Lugar y tipos con su cantidad |
| `pii_detected` | Datos personales redactados | Lugar y tipos con su cantidad |
| `tool_call` | Cada herramienta ejecutada | Herramienta y argumentos validados |
| `output_rejected` | Respuesta del modelo fuera del protocolo | Motivo |
| `output_sanitized` | Respuesta limpiada | Cambios |

**Cabeceras de seguridad en toda respuesta (`app/headers.py`).** El middleware se agrega al final, así que
también cubre los `401`, `413`, `422`, `429`, `500` y `502`.

- Comunes: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy` y
  `Cross-Origin-Resource-Policy` en `same-origin`, `Permissions-Policy` sin cámara, micrófono, ubicación, pagos
  ni USB, y `Cache-Control: no-store`.
- CSP de `/`: solo el `<script>` y el `<style>` en línea de la página, por su hash SHA-256 calculado al arrancar;
  `fetch` solo al mismo origen. Si se cambia la página, el hash cambia solo.
- CSP de `/docs`: Swagger UI desde cdn.jsdelivr.net y su script de arranque en línea. Esa página no tiene datos de
  personas.
- CSP del resto, que es JSON: `default-src 'none'`.
- En producción, `Strict-Transport-Security`.
- `/redoc` se apaga (`/docs` basta); el contenedor corre uvicorn con `--no-server-header`.
- **CORS cerrado:** no hay `CORSMiddleware` y ninguna respuesta lleva `Access-Control-Allow-Origin`.

**Topes que producción no puede quitar** (`app/compliance.py`): `MAX_BODY_BYTES` ≤ 1 MiB,
`MAX_HISTORY_MESSAGES` ≤ 50, `AGENT_MAX_STEPS` ≤ 5 y `MODEL_MAX_TOKENS` ≤ 1024. Si alguno se pasa, producción no
arranca y se audita `startup_refused`.

## Consecuencias

- Una petición sospechosa no gasta modelo ni cuota y deja un rastro buscable por `request_id`, con qué regla la
  detuvo.
- **Falsos positivos.** Una pregunta legítima que use frases como «ignora las reglas» o «muéstrame los secretos»
  se bloquea. Las pruebas cubren preguntas normales de RH y todas las respuestas reales del asistente, que la
  página devuelve en el historial. La página pide reformular.
- **Falsos negativos.** Las heurísticas no detectan una inyección parafraseada o en otro idioma. Lo que queda lo
  contienen las capas del agente: herramientas de solo lectura, argumentos validados, tope de pasos y salida
  limpia y redactada. Un clasificador con un modelo sería la mejora siguiente, con su costo y su latencia.
- `startup` aparece una vez por proceso (con varios workers, una por cada uno).
- Cambiar la página no requiere tocar la CSP; agregar otro script en línea o externo, sí.

## Alternativas descartadas

- **Solo marcar la inyección y dejarla pasar al modelo**: gasta cuota y depende de que el modelo resista.
- **`'unsafe-inline'` en la página**: anularía el valor de la CSP; los hashes cuestan una línea de código.
- **Servir Swagger UI desde el contenedor**: quitaría la excepción del CDN, pero agrega archivos estáticos que
  mantener. Queda como mejora.
