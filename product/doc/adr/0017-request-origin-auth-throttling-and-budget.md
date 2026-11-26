# 0017 · Origen de las peticiones, límite de claves fallidas y presupuesto diario

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción · Completa el ADR 0010

## Contexto

El ADR 0010 limita a cada cliente **autenticado** (`429` con `Retry-After`). Quedaban dos huecos:

- **Fuerza bruta de claves.** Las peticiones sin clave o con una clave equivocada no tenían límite. Alguien podía
  probar claves sin freno; la comparación en tiempo constante evita filtrar la clave por tiempos, pero no impide
  probar muchas.
- **Consumo sin techo** (OWASP LLM10). El límite por minuto acota la velocidad, no el gasto: un cliente dentro de
  su límite podía gastar todo el día.

Para limitar las fallas hay que saber de dónde vienen, y en Render la aplicación está detrás de un proxy. La
documentación de Render no dice qué cabeceras pone con la IP del cliente (revisado el 2026-11-26).

## Decisión

**Origen** (`app/security.py` · `client_origin`):

- Cada proxy agrega a la derecha de `X-Forwarded-For` la IP de quien le habló. Con `TRUSTED_PROXY_HOPS=N`, el
  origen es el N-ésimo valor desde la derecha: el que agregó el proxy más lejano de confianza.
- Los valores de la izquierda los puede escribir el cliente; no cuentan, así que inventarlos no cambia el origen.
- Con `TRUSTED_PROXY_HOPS=0` (por omisión, en local), o si el encabezado trae menos valores, el origen es la IP
  del socket.
- `render.yaml` declara `TRUSTED_PROXY_HOPS=1`: se supone **un** proxy de Render delante de la aplicación.

**Límite de claves fallidas por origen:**

- Más de `AUTH_FAILURES_PER_MINUTE` claves faltantes o equivocadas (10 por omisión) desde un origen en 60 s
  responden `429 {"detail": "too many failed authentication attempts"}` con `Retry-After`.
- El límite se revisa **antes** de comparar la clave: un origen bloqueado no puede seguir probando, ni siquiera con
  la clave correcta, hasta que salga de la ventana.
- Se audita como `auth_throttled`.
- La memoria está acotada a 10 000 orígenes: cuando se llena, se olvidan los que ya no tienen fallas recientes.

**La IP es un dato personal.** Nunca va al log ni a las trazas. `auth_failed` y `auth_throttled` llevan `origin`:
una huella HMAC-SHA256 de 16 caracteres con una clave aleatoria por proceso. Sirve para correlacionar los
eventos de un mismo origen dentro de un proceso, y no permite recuperar la IP ni cruzarla entre reinicios.

**Presupuesto diario por cliente:**

- `CLIENT_DAILY_BUDGET_USD` (1.0 por omisión) es el gasto máximo de cada cliente por día UTC.
- Cuando se agota: `429 {"detail": "daily budget exceeded"}` con `Retry-After` hasta la medianoche UTC, auditado
  como `budget_exceeded`.
- El costo de cada respuesta se suma al terminarla, así que una sola petición puede pasar el presupuesto por su
  propio costo.
- Compliance: en producción el presupuesto debe ser mayor que 0 (no se puede apagar).

La página explica cada `429` en español según su motivo: demasiadas preguntas, demasiadas claves equivocadas o
presupuesto agotado.

## Consecuencias y riesgos

- Detrás de un NAT corporativo, muchas personas comparten un origen: una persona que se equivoca de clave muchas
  veces bloquea a las demás por un minuto. Es el precio de frenar la fuerza bruta; el límite es configurable.
- **Si la suposición del proxy es falsa:**
  - Si Render encadenara dos proxies, el último valor sería la IP del primer proxy. Todas las peticiones
    compartirían un origen y el límite de fallas sería global: un atacante bloquearía a todos por un minuto.
    La fuerza bruta seguiría frenada.
  - Si Render no agregara `X-Forwarded-For`, el origen sería la IP del socket, que también es la del proxy, con
    el mismo efecto.
  - Ninguno de los dos casos deja pasar más intentos.
- Contadores, límite y presupuesto viven en la memoria de cada proceso (ADR 0001 y 0005): con varios workers o
  réplicas, cada uno cuenta por su lado y se reinician al reiniciar. Para compartirlos haría falta un almacén
  como Redis; queda como mejora.

## Comprobación en producción (fase final)

1. Desde tu equipo: `scripts/security_check.py <url>` termina mandando claves equivocadas y debe recibir `429`.
2. Justo después, desde otra red (por ejemplo, el celular con datos móviles), una petición sin clave debe recibir
   `401`, no `429`. Si recibe `429`, Render no está poniendo el origen como supone este ADR: hay que revisar
   `TRUSTED_PROXY_HOPS`.
3. En Render Logs, las líneas `auth_failed` y `auth_throttled` de tu equipo comparten la misma huella `origin` y
   ninguna línea contiene una IP.
