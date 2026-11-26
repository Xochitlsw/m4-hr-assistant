# 0003 · Inyección de dependencias y configuración validada al arrancar

Fecha: 2026-11-25 · Estado: aceptada · Sesión 1 (actualizada en las sesiones 2, 3 y 4)

## Contexto

`QA.md` pide inyección de dependencias y comportamiento determinista. Las pruebas deben poder cambiar el modelo,
los datos y la configuración sin variables de entorno globales ni red. Un error de configuración debe verse al
desplegar, no con la primera pregunta.

## Decisión

- `create_app(settings=None, model=None, directory=None)` arma la aplicación con lo que recibe; si falta algo, lo
  construye desde el entorno. `app = create_app()` existe a nivel de módulo para `uvicorn app.main:app`.
- `load_settings(env)` lee un mapeo inyectado (por omisión `os.environ`), aplica los valores por omisión de la
  especificación y lanza `ConfigError` con un mensaje claro ante un valor inválido (por ejemplo
  `AGENT_MAX_STEPS=0`). El proceso no arranca.
- Un `EMPLOYEES_PATH` relativo se resuelve contra la raíz del producto, no contra el directorio actual.
- Sesión 2: `create_app()` también recibe `observability`, un objeto con el reloj (`Clock`), el logger de
  eventos, las métricas y las trazas, con los reales por omisión. Así las pruebas fijan la hora y leen el log sin
  depender de `stdout` ni del tiempo real. La configuración se agrupa por tema (`settings.observability`).
- Sesión 3: `create_app()` también recibe el `httpx.Client` del modelo real (las pruebas pasan uno con
  `MockTransport`). Si recibe `model`, ese modelo atiende las dos rutas del enrutador (lo usan las pruebas). Se
  agregan los grupos `settings.models` (con `gateway`) y `settings.routing`. Los precios (`PRICES_PATH`) se validan
  al arrancar, igual que los datos. Los dos modelos con su precio los arma `app/catalog.py` (fuera de `llm.py`
  para evitar importaciones circulares).
- Sesión 4: se agrega el grupo `settings.security` (entorno, clientes y claves fuera de `repr`, límite por minuto)
  y `model_provider` pasa a `settings.models.provider`, para que `Settings` no supere 7 grupos. Después de validar
  los valores, `create_app()` aplica las reglas de producción (ADR 0011): validar la forma de un valor y decidir si
  una configuración válida es aceptable en producción son pasos distintos.

## Consecuencias

- Las pruebas usan `create_app()` con un modelo, un directorio y una configuración propios, y `TestClient`.
- Un despliegue mal configurado falla al iniciar y el mensaje dice qué variable corregir.
- La configuración queda en un solo lugar, lista para crecer con más variables (modelo real, límites,
  observabilidad).
