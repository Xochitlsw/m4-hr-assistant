# 0005 · Métricas en memoria por proceso y SLO de latencia

Fecha: 2026-11-25 · Estado: aceptada · Sesión 2

## Contexto

La especificación pide `GET /metrics` de este proceso: peticiones, tasa de error, p50 y p95 de latencia, tokens y
si se cumple la meta `SLO_P95_MS`. No hay base de datos ni sistema de métricas externo, y la API es sin estado.

## Decisión

- `Metrics` vive en memoria, una por proceso, protegida con un candado porque `POST /ask` corre en el pool de
  hilos de FastAPI.
- Cuenta el tráfico de usuarios: todas las rutas menos `/health` y `/metrics`.
- `error_rate` = respuestas `5xx` / peticiones contadas; los `4xx` son errores del cliente y no cuentan.
- p50 y p95 por rango más cercano sobre una ventana de las últimas 1000 latencias (tamaño inyectable). Sin datos
  valen 0.
- `slo.met` es `true` si el p95 está por debajo de `SLO_P95_MS`. `tokens` suma entrada y salida de cada corrida del
  agente. La respuesta incluye el `pid`.

## Consecuencias

- Sin dependencias nuevas y con memoria acotada.
- El chequeo de salud de la plataforma no diluye la latencia ni el SLO.
- Cada copia reporta lo suyo y los contadores se reinician con cada despliegue. Una vista agregada (tablero, varias
  copias) queda para después.
- La ventana hace que el p95 refleje lo reciente, no toda la vida del proceso.
