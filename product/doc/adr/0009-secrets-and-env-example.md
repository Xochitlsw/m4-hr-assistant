# 0009 · Secretos solo en el entorno y `.env.example` sin valores

Fecha: 2026-11-25 · Estado: aceptada · Sesión 3

## Contexto

Con el modelo real aparece un segundo secreto, `CF_API_TOKEN`, además de `LANGSMITH_API_KEY` (ADR 0006). A
diferencia de LangSmith, el producto sí necesita leer el valor del token para llamar al gateway. Un token filtrado
en el repositorio, el log o un mensaje de error permite gastar la cuenta de otra persona.

## Decisión

- Los secretos solo vienen del entorno: `.env` en el equipo local (ignorado por git) y variables secretas en la
  plataforma de despliegue. Nunca en el código, el repositorio, el log ni las pruebas (que usan valores ficticios).
- El token se guarda en la configuración fuera de `repr` y no se registra. Si falta una variable `CF_*` con
  `MODEL_PROVIDER=gateway`, el mensaje de `ConfigError` nombra la variable, nunca un valor.
- `product/.env.example` lista todas las variables del producto con sus valores por omisión y los secretos vacíos.
  Quien corre el producto lo copia a `.env` y lo llena en su editor.

## Consecuencias

- Correr con el modelo real es `uv run --env-file .env uvicorn app.main:app` dentro de `product/`.
- Revisar que un secreto no se filtre es comprobar el log, los errores y el `git status`, no confiar en la memoria
  de nadie; el escaneo automático de secretos queda para el CI del reto.
- Rotar un token no requiere cambiar código, solo el entorno.
