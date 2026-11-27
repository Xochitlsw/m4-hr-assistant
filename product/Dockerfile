# HR Assistant: imagen de producción (ADR 0012 y 0015).
# python:3.12-slim, usuario sin privilegios y solo el código y los datos de la app.
# Solo las dependencias de ejecución, del lock con versiones y hashes: nada de pytest,
# pylint ni Playwright en producción.
# Imagen base fijada por digest (índice multiarquitectura de python:3.12-slim publicado
# el 2026-11-24); cómo se actualiza: ADR 0015.
FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Primero las dependencias: la capa se reutiliza mientras requirements.txt no cambie.
# --require-hashes: pip rechaza cualquier archivo cuyo hash no coincida con el lock.
# --no-deps: el lock ya trae todas las dependencias; pip no resuelve nada por su cuenta.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt

# El código y los datos quedan del usuario root: el proceso los lee, no los cambia.
COPY app/ app/
COPY data/ data/

# Usuario sin privilegios y sin shell de inicio de sesión.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
USER app

EXPOSE 8000

# Forma shell: sh expande ${PORT:-8000}. Usa Python: la imagen slim no trae curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as request; \
request.urlopen('http://127.0.0.1:${PORT:-8000}/health', timeout=4)"

# sh -c expande ${PORT:-8000} (Render lo define); exec deja a uvicorn como proceso
# principal para que reciba la señal de apagado. --no-access-log y --log-level warning:
# el log de la plataforma queda solo con las líneas JSON del producto (sin el acceso ni
# las líneas de arranque de uvicorn; sus advertencias y errores siguen saliendo).
CMD ["sh", "-c", \
     "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --no-server-header --log-level warning"]
