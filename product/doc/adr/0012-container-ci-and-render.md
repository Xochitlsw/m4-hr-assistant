# 0012 · Contenedor, pipeline de CI encadenado y Blueprint de Render

Fecha: 2026-11-25 · Estado: aceptada · Sesión 4

## Contexto

El producto debe publicarse de forma repetible, sin secretos en el repositorio y solo cuando las pruebas pasan.
Los ingenieros deben ver en qué etapa falló un cambio. Render ofrece un plan gratuito con contenedores y despliegue
condicionado a los checks del repositorio.

## Decisión

- **Contenedor**:
  - `python:3.12-slim` con un usuario sin privilegios.
  - Solo copia `app/` y `data/`.
  - Arranca con `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log`, para que el log de la
    plataforma tenga solo líneas JSON.
  - `HEALTHCHECK` con Python.
  - `.dockerignore` deja fuera `.env`, pruebas, cachés y lo que no se ejecuta.
- **CI** en cada push y pull request, con cuatro trabajos encadenados por `needs`:
  1. `style` (pylint, 88 columnas).
  2. `unit` (pruebas y BDD, cobertura >= 80 %).
  3. `e2e` (Playwright contra el producto con el modelo simulado y una clave de ejemplo).
  4. `docker` (construcción de la imagen).

  Una etapa no corre si la anterior falló.
- **Render**:
  - Blueprint (`render.yaml`) con un servicio web que se llama igual que el repositorio.
  - `runtime: docker`, `plan: free`, `healthCheckPath: /health` y `autoDeployTrigger: checksPass`: Render publica
    solo si el CI pasa.
  - Variables no secretas en el Blueprint (`ENVIRONMENT=production`, `MODEL_PROVIDER=gateway`, `HIDE_*=true`).
  - Los secretos (`API_KEYS`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`) con `sync: false`: se escriben en Render.
  - `LANGSMITH_API_KEY` no va, porque Render exige un valor para cada secreto al crear el Blueprint.
- **Prueba de humo**: `scripts/smoke.py <url>` comprueba `/health`, `/ask` sin clave (`401`) y con clave (`200`),
  con la clave en `SMOKE_API_KEY`.
- **Ubicación**: el CI y el Blueprint viven dentro de `product/`, como si `product/` fuera la raíz de su propio
  repositorio.

## Consecuencias

- Publicar es un `git push`: el CI decide si Render despliega; nada llega a producción sin pasar las cuatro
  etapas.
- El plan gratuito duerme el servicio tras 15 minutos sin uso: la primera petición tarda hasta un minuto (la prueba
  de humo espera 90 s).
- La imagen incluye las librerías de prueba porque el producto usa un solo `requirements.txt`; separar dependencias
  de ejecución y de desarrollo achicaría la imagen.
- En un repositorio donde `product/` es una subcarpeta, GitHub solo lee `.github/workflows/` de la raíz y el
  Blueprint debe indicar `rootDir: product`; mover ambos es necesario antes de publicar desde ese repositorio.
