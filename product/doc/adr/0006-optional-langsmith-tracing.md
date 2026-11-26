# 0006 · Trazas opcionales en LangSmith, sin texto por omisión

Fecha: 2026-11-25 · Estado: aceptada · Sesión 2

## Contexto

El log dice qué pasó, pero no cómo razonó el agente paso a paso. LangSmith muestra cada corrida como un árbol
(agente, modelo, herramientas). Es un servicio externo: requiere una clave secreta, sale a la red y, si se manda el
texto, recibe datos personales de empleados.

## Decisión

- Las trazas se encienden solo si `LANGSMITH_TRACING=true` **y** existe `LANGSMITH_API_KEY`. Si no, cada petición
  corre en `tracing_context(enabled=False)`: no se crea cliente y no hay red.
- El producto nunca lee el valor de la clave: el cliente de LangSmith la toma del entorno y la configuración solo
  guarda si las trazas están encendidas.
- `HIDE_INPUTS` y `HIDE_OUTPUTS` (`true` por omisión) se pasan a `Client(hide_inputs=..., hide_outputs=...)`.
- Se traza en los bordes del agente, no dentro del modelo: la corrida (`chain`), cada llamada al modelo (`llm`) y
  cada herramienta (`tool`). Cualquier modelo inyectado queda trazado igual. El `request_id` va en los metadatos
  del contexto de la petición.

## Consecuencias

- Sin clave, el producto y sus pruebas funcionan igual y sin red.
- Con los valores por omisión, LangSmith ve la estructura, las latencias y los metadatos, pero no el texto. Ver el
  contenido exige apagar `HIDE_*` a propósito, o en el futuro redactar los datos personales en lugar de ocultarlo
  todo.
- La clave es un secreto de la plataforma (`.env` local o variables de Render), nunca del repositorio.
- En una red con inspección TLS, las trazas desde el equipo local pueden fallar por certificado; no afecta a las
  pruebas ni al despliegue.
