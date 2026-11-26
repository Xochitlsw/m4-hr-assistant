# 0002 · Interfaz del modelo, protocolo JSON y bucle con tope

Fecha: 2026-11-25 · Estado: aceptada · Sesión 1

## Contexto

El modelo cambiará (hoy solo hay uno simulado; después, uno real por red). El agente no debe saber cuál usa, y
su bucle no puede quedarse dando vueltas ni ejecutar lo que el modelo invente.

## Decisión

- El agente habla con el modelo solo a través de `complete(messages) -> Completion` (`text`, `input_tokens`,
  `output_tokens`). `build_model(settings, directory)` elige la implementación según `MODEL_PROVIDER`.
- `FakeModel` (`MODEL_PROVIDER=fake`) es determinista, sin red ni costo, con las 4 reglas de la especificación;
  cuenta tokens por palabras.
- El modelo responde un único objeto JSON: `{"tool": ..., "args": {...}}` o `{"answer": ...}`. El resultado de una
  herramienta vuelve como mensaje `user` que empieza con `Resultado de la herramienta: `.
- Un paso es una llamada al modelo; el bucle se corta en `AGENT_MAX_STEPS` con la frase fija de la
  especificación.
- Solo se ejecutan las herramientas de una lista blanca (`get_employee`, `search_by_department`) con argumentos
  validados; una salida no válida consume un paso y se le devuelve al modelo como error.

## Consecuencias

- Cambiar de modelo es agregar una implementación de `complete` y un valor de `MODEL_PROVIDER`; el agente y sus
  pruebas no cambian.
- Las pruebas del agente y del contrato son deterministas y no gastan cuota.
- El costo y la latencia por pregunta tienen un máximo conocido (`AGENT_MAX_STEPS` llamadas).
- El protocolo depende de que el modelo real devuelva JSON válido; el tope y la validación contienen sus fallas.
