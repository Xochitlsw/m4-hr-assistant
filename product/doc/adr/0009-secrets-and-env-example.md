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

## Actualización (2026-11-26): ninguna clave en la línea de comandos

Una clave escrita en un comando (`VARIABLE='valor' comando` o una asignación con `$env:`) queda en el historial de
la consola; PowerShell lo guarda en texto plano en disco. Por eso:

- Los ejemplos arrancan el producto con `uv run --env-file .env ...` y leen la clave de la prueba de humo sin eco
  (`Read-Host -AsSecureString` en PowerShell, `read -rs` en Bash), borrándola de la sesión al terminar.
- `scripts/smoke.py` solo acepta la clave en `SMOKE_API_KEY`, que también lista `.env.example` (vacía); rechaza un
  argumento de más y su mensaje de uso no muestra una clave en el comando.
- `.gitignore` ignora `.env` y cualquier `.env.*`, salvo `.env.example`.
- `tests/test_secrets.py` lo vuelve regla: los secretos de la plantilla siguen vacíos y ningún README, documento o
  mensaje de la prueba de humo pone una clave dentro de un comando. Sus fallas nombran variables y líneas, nunca
  un valor.
