# 0013 · Validación de entradas y de salidas

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción

## Contexto

La API ya validaba el largo de la pregunta, la forma del historial (roles `user`/`assistant`, sin campos extra), la
lista blanca de herramientas y que el modelo respondiera en el protocolo JSON (ADR 0002). Quedaban huecos:

- El historial lo manda el cliente y no tenía tope de mensajes ni de largo: un cliente podía mandar megabytes que
  la API leía, validaba y pasaba a la ventana de historial (palanca de costo, ADR 0007).
- El cuerpo de la petición no tenía tope: se leía completo antes de validar la clave o los campos.
- Ningún texto se revisaba por caracteres de control o marcas bidireccionales, que sirven para esconder
  instrucciones a quien lee el texto (ataques de tipo «Trojan Source») o para ensuciar consolas.
- Los argumentos de las herramientas solo se revisaban como texto: un `employee_id` o un departamento arbitrario
  volvía al modelo dentro del resultado de la herramienta (un canal para reflejar instrucciones).
- La respuesta del modelo salía tal cual: sin largo máximo y con lo que el modelo escribiera. La página la pinta con
  `textContent`, pero otros clientes de la API podrían interpretar HTML.

## Decisión

**Entradas: se rechazan (`413` o `422`).** No se adivina qué quiso decir el cliente.

- `MAX_BODY_BYTES` (64 KiB por omisión): un middleware ASGI (`app/body_limit.py`) responde `413` si
  `Content-Length` declara más, y cuenta los bytes si el cuerpo llega por partes. Corre antes que la clave y que la
  validación; el middleware de observabilidad lo envuelve, así que el `413` lleva `X-Request-ID` y queda en el log.
- `MAX_HISTORY_MESSAGES` (20): mensajes de historial aceptados.
- Cada mensaje del historial mide como mucho el tope de su rol: `user`, `MAX_QUESTION_CHARS`; `assistant`,
  `MAX_ANSWER_CHARS`. Nada más largo pudo salir de una conversación real con esta API.
- La pregunta y cada mensaje rechazan caracteres de control (categoría Unicode `Cc`, salvo tabulador y saltos de
  línea) y las marcas bidireccionales U+202A a U+202E y U+2066 a U+2069 (`app/sanitize.py`).
- La página manda solo los últimos 20 mensajes y explica el `413` y el `422` en español.

**Salidas del modelo hacia las herramientas: se rechazan dentro del bucle.** Cada respuesta fuera del protocolo
consume un paso, vuelve al modelo como error sin repetir lo que escribió y se audita como `output_rejected` con un
motivo corto: `not_json`, `bad_shape`, `empty_answer`, `unknown_tool`, `bad_args`, `bad_employee_id` o
`unknown_department`. Los argumentos se validan y normalizan antes de ejecutar: `employee_id` con la forma
`EMP-0000` (sin distinguir mayúsculas ni espacios) y `department` entre los departamentos de los datos (sin
distinguir mayúsculas ni acentos; el error lista los que existen).

**Salida hacia la persona: se limpia.** La persona ya espera una respuesta, así que en lugar de rechazarla se
quitan los caracteres prohibidos y las etiquetas HTML (solo lo que parece una etiqueta: `< 5000` se queda), se
recorta a `MAX_ANSWER_CHARS` con la marca « [respuesta recortada]» y, si no queda nada, se responde el texto seguro
del tope de pasos. Si cambió algo, se audita `output_sanitized` con la lista de cambios. Nunca se registra el
texto. Esta es la única salida de `AskService`: la redacción de datos sensibles se conecta aquí.

## Consecuencias

- Tres variables nuevas, validadas al arrancar: `MAX_HISTORY_MESSAGES` (>= 0), `MAX_ANSWER_CHARS` (>= 100, para que
  quepan los textos seguros y la marca) y `MAX_BODY_BYTES` (>= 1024).
- Si se baja `MAX_HISTORY_MESSAGES` de 20, la página falla con `422` en conversaciones largas: el tope de la página
  y el de la API van juntos.
- Un departamento que no existe ya no llega a la herramienta: el modelo recibe la lista de los que existen y gasta
  un paso; con `AGENT_MAX_STEPS=3` todavía puede responder.
- Las pruebas cubren cada regla (`tests/test_sanitize.py`, `tests/test_validation.py`, `tests/test_agent.py`) y la
  página se prueba con una conversación de 12 preguntas (`e2e/test_page.py`).

## Alternativas descartadas

- **Rechazar respuestas del modelo con HTML o demasiado largas**: obligaría a otra llamada al modelo (costo y
  latencia) para algo que se arregla limpiando.
- **Escapar el HTML en vez de quitarlo**: la página ya usa `textContent`; para otros clientes, quitarlo es más
  simple de razonar que un texto con entidades.
- **Rechazar todos los caracteres de formato (categoría `Cf`)**: rompería emojis compuestos y texto legítimo; solo
  se rechazan las marcas bidireccionales.
