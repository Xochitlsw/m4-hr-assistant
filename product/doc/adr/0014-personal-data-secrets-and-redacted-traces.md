# 0014 · Datos personales y secretos: redactar, bloquear y trazas redactadas

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción · Cambia una regla del ADR 0011

## Contexto

El asistente responde con datos del directorio de RH. Esos datos (id, nombre, puesto, departamento, fecha de
ingreso y estado) son su propósito: no son sensibles para quien usa la API. Pero la persona puede escribir otros
datos en la pregunta o en el historial (una CURP, un correo, un teléfono, una tarjeta) o pegar por error un
secreto (una clave de LangSmith, un token). Sin control:

- Esos datos viajan al proveedor del modelo (Cloudflare Workers AI) y, si las trazas están encendidas, a LangSmith.
- El modelo puede repetirlos en la respuesta.
- Un secreto pegado en una pregunta queda expuesto en el proveedor y en cualquier copia de la conversación.

Hasta la sesión 4 la única protección era no registrar el texto en el log y **ocultar** entradas y salidas en las
trazas (`HIDE_INPUTS=true` y `HIDE_OUTPUTS=true`, obligatorios en producción con trazas: ADR 0011 y
`spec/04-production.md`). Ocultar protege, pero deja trazas sin contenido: para depurar una respuesta equivocada
no se ve qué preguntó la persona ni qué respondió el modelo.

## Decisión

**Un solo detector** (`app/privacy.py`), por patrones, para la entrada, la salida y las trazas:

- Secretos: clave privada, JWT, llave de AWS, token de GitHub, clave de LangSmith, `Bearer` y asignaciones del
  tipo palabra clave, dos puntos o signo igual y un valor (`password`, `token`, `contraseña`, `clave`...).
- Datos personales de México: CURP, RFC, correo, tarjeta (solo si pasa Luhn), CLABE, NSS y teléfono.
- Mira una copia normalizada del texto (`detection_view`: sin caracteres de formato invisibles, en NFKC y con
  dígitos ASCII), para que un espacio de ancho cero o un dígito de otro alfabeto no lo esquive.
- Reemplaza cada hallazgo por `[REDACTED:TIPO]`. La auditoría registra tipo, cantidad y lugar, nunca el valor.

**Política por lugar** (decisión de la persona):

| Lugar | Secreto | Dato personal |
| --- | --- | --- |
| Pregunta e historial | Bloquea: `400 {"detail": "request contains a secret; remove it and ask again"}`, audita `secret_blocked`; el modelo no recibe nada | Se redacta antes de llegar al modelo; audita `pii_detected` |
| Respuesta | Se redacta | Se redacta; audita `pii_detected` (lugar `answer`) |
| Log | Nunca se escribe texto (ADR 0004) | Igual |
| Trazas de LangSmith | Se redacta | Se redacta |

Los registros del directorio no tienen ninguna de esas formas y nunca se redactan; una prueba recorre todos los
empleados para comprobarlo. Los resultados de las herramientas no pasan por la redacción de la entrada porque vienen
del directorio, no de la persona.

**Trazas redactadas en vez de ocultas.** `HIDE_INPUTS` y `HIDE_OUTPUTS` aceptan ahora tres valores:

- `true`: ocultar esa dirección (como en la sesión 2; sigue siendo el valor por omisión).
- `redact`: mandarla redactada. El cliente de LangSmith recibe `redact_tree` como `anonymizer`, que LangSmith
  aplica al JSON ya serializado de entradas, salidas, metadatos y errores. Lo que está oculto sigue oculto.
- `false`: mandarla en claro. Solo para desarrollo.

Este despliegue usa `redact` en las dos direcciones (`render.yaml` y `.env.example`).

## Cambio frente a la regla de la sesión 4

`spec/04-production.md` pide que producción no arranque si las trazas están encendidas y `HIDE_INPUTS` o
`HIDE_OUTPUTS` no valen `true`. La regla pasa a ser: **con trazas en producción, cada dirección se oculta o se
redacta; nunca en claro**. Producción sigue sin arrancar con `false`, que es el caso que la regla original quería
impedir (texto de personas en un servicio externo). La nueva regla es un refinamiento, no un relajamiento sin
control: `redact` solo existe con el detector y sus pruebas. Está en `app/compliance.py`, en
`doc/compliance-checklist.md` y en `tests/test_compliance.py`. Ninguna verificación de la fábrica comprueba esa
regla; la comprueban las pruebas del producto.

## Riesgo que queda

- **Falsos negativos.** Un detector por patrones no ve lo que no tiene forma: un domicilio, un nombre de
  familiar, un diagnóstico médico o un dato en un formato no previsto. Con `redact` ese texto llega a LangSmith;
  con `true` no. Es el precio de tener trazas útiles.
- **Falsos positivos.** Un número de 10 u 11 dígitos sin relación (un folio) puede redactarse como teléfono o NSS.
  Para la persona es una molestia, no una fuga.
- **El proveedor del modelo** recibe el texto redactado, pero ve los resultados de las herramientas (datos del
  directorio), como antes.
- Mitigaciones: clave de LangSmith de tipo *Tracing Only*, un proyecto por entorno, `true` disponible en cualquier
  momento sin cambiar código, pruebas de positivos y negativos del detector (`tests/test_privacy.py`) y de las
  trazas con el cliente real de LangSmith sobre una sesión falsa (`tests/test_tracing.py`).

## Alternativas descartadas

- **Seguir ocultando todo**: seguro, pero las trazas no sirven para depurar, que es lo que el reto evalúa.
- **Bloquear cualquier dato personal**: la persona de RH escribiría de nuevo la pregunta sin entender por qué; un
  dato redactado no impide responder sobre el directorio.
- **Microsoft Presidio**: detecta más tipos (nombres, lugares) con modelos de lenguaje, pero agrega spaCy y sus
  modelos (cientos de MB) a una imagen pequeña en un plan gratuito, y sus reconocedores para México no cubren
  CURP ni CLABE sin configuración extra. Queda como mejora futura.
