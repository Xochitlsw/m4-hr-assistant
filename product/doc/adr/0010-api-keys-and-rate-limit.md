# 0010 · Clave por cliente y límite de peticiones

Fecha: 2026-11-25 · Estado: aceptada · Sesión 4

## Contexto

Publicado en internet, cualquiera podría usar el asistente, gastar la cuota del modelo real y consultar datos de
empleados. Hace falta saber quién pregunta, impedir el acceso sin permiso y evitar que un solo cliente agote el
servicio. La API sigue sin estado (ADR 0001) y sin base de datos.

## Decisión

- `API_KEYS=cliente:clave,...` define los clientes. `POST /ask` y `GET /metrics` exigen `X-API-Key`; sin clave o
  con una inválida responden `401 {"detail": "invalid or missing API key"}`. `/`, `/health` y `/docs` no la piden.
- La clave presentada se compara con todas las claves con `hmac.compare_digest`. Nunca se escribe en el log, la
  traza, un error ni el código; `API_KEYS` mal formada impide arrancar con un mensaje que da el número de entrada y
  su largo, nunca su valor.
- La autenticación es una dependencia de FastAPI declarada con `APIKeyHeader(auto_error=False)`, para responder
  el `401` con el texto de la especificación. Corre antes de validar los campos del cuerpo: un JSON válido pero
  incompleto sin clave recibe `401`; un JSON malformado recibe `422` antes de la clave, sin revelar datos. Queda
  documentada en OpenAPI.
- Límite por cliente en `POST /ask`: ventana deslizante de 60 s con el reloj inyectado; al pasar
  `RATE_LIMIT_PER_MINUTE` responde `429 {"detail": "rate limit exceeded"}` con `Retry-After` en segundos enteros.
- El nombre del cliente va en el log (`client`), la auditoría y los metadatos de la traza. Se auditan también los
  rechazos: `auth_failed` (motivo y ruta) y `rate_limited` (cliente y espera).

## Consecuencias

- Cada consumidor (un portal, una prueba de humo, una persona) tiene su propia clave, que se puede revocar sin
  afectar a los demás.
- El límite vive en memoria de cada proceso: con N copias el límite efectivo es N veces mayor y se reinicia con
  cada despliegue. Un límite compartido (Redis o el propio gateway) es una mejora para cuando haya varias copias.
- Las claves se rotan cambiando la variable en la plataforma y reiniciando; no hay que tocar código.
- Una clave compartida por varias personas no distingue quién preguntó; cada consumidor debe tener la suya.
