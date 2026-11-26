# 0003 · Inyección de dependencias y configuración validada al arrancar

Fecha: 2026-11-25 · Estado: aceptada · Sesión 1

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

## Consecuencias

- Las pruebas usan `create_app()` con un modelo, un directorio y una configuración propios, y `TestClient`.
- Un despliegue mal configurado falla al iniciar y el mensaje dice qué variable corregir.
- La configuración queda en un solo lugar, lista para crecer con más variables (modelo real, límites,
  observabilidad).
