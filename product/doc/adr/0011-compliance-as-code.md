# 0011 · Compliance como código: producción se niega a arrancar si no cumple

Fecha: 2026-11-25 · Estado: aceptada · Sesión 4

## Contexto

Las reglas de producción (no usar el modelo simulado, exigir claves fuertes, no mandar datos personales a un
tercero) se pueden olvidar al configurar una plataforma a mano. Una lista en un documento no impide que alguien
despliegue mal; un programa sí.

## Decisión

- Con `ENVIRONMENT=production`, `create_app()` revisa la configuración ya validada y **no arranca** si:
  1. `MODEL_PROVIDER` es `fake`.
  2. `API_KEYS` está vacía o alguna clave tiene menos de 16 caracteres (el mensaje nombra al cliente, no la clave).
  3. `LANGSMITH_TRACING=true` y `HIDE_INPUTS` o `HIDE_OUTPUTS` no valen `true`. Se revisa la bandera aunque falte la
     clave de LangSmith, para que agregarla después no pueda encender trazas con texto.
- El error reúne todas las reglas que fallan:
  `ENVIRONMENT=production refuses to start: <regla>; <regla>`.
- `doc/compliance-checklist.md` relaciona cada regla con su lugar en el código y con la prueba que la comprueba.
- Con `ENVIRONMENT=development` (por omisión) las reglas no se aplican, para poder probar con el modelo simulado.

## Consecuencias

- Un despliegue que incumple falla en el arranque y la plataforma lo muestra como fallido, en lugar de atender
  peticiones de forma insegura.
- Agregar una regla es código con su prueba; la lista y el comportamiento no se separan.
- Las reglas cubren configuración, no comportamiento en tiempo de ejecución (por ejemplo, datos personales en una
  respuesta): eso necesita otros controles.
