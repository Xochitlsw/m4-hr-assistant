# 0022 · Palancas de costo en producción, caché del gateway y ablación

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción · Completa los ADR 0007, 0008 y 0017

## Contexto

Desde la sesión 3 el producto tiene cuatro palancas de costo: enrutamiento por regla, ventana de historial, tope
de salida y caché del gateway. `verification evaluate` mide las dos primeras juntas con el modelo simulado: misma
calidad y 86.2 % menos costo. Para producción faltaba:

- **Fijar en el Blueprint qué palancas están activas.** Producción no debe depender de valores por omisión.
- **Saber cuánto ahorra cada palanca por separado**, con la misma medición del juez.
- **Decidir la caché.** El ADR 0008 la dejó apagada porque el gateway no tenía autenticación propia. Hoy el
  gateway exige su autenticación (actualización del ADR 0008) y solo responde a llamadas con el token.
- **Que `CACHE_TTL_SECONDS=0` signifique «sin caché»** aunque el gateway tenga la caché encendida en su panel. Sin
  eso, la línea base de la muestra real podría salir de la caché.
- **Evitar que el `request_id` rompa la caché.** Va en `cf-aig-metadata` en cada llamada (ADR 0021); si el
  gateway lo incluyera en la clave, la caché nunca respondería.

## Decisión

**Palancas activas en producción** (`render.yaml`, probadas en
`tests/test_delivery.py::test_production_keeps_every_cost_lever_on_and_passes_compliance`):

| Variable | Valor | Palanca |
| --- | --- | --- |
| `ROUTING` | `true` | Enrutamiento por regla (ADR 0007) |
| `HISTORY_WINDOW` | `6` | Ventana de 3 turnos (ADR 0007) |
| `MODEL_MAX_TOKENS` | `300` | Tope de salida (ADR 0008) |
| `CACHE_TTL_SECONDS` | `300` | Caché del gateway, 5 minutos (este ADR) |
| `CLIENT_DAILY_BUDGET_USD` | `1.0` | Techo de gasto por cliente (ADR 0017) |

**Caché del gateway** (`app/gateway.py`):

- Con `CACHE_TTL_SECONDS` > 0 se mandan dos cabeceras:
  - `cf-aig-cache-ttl` con la TTL.
  - `cf-aig-cache-key` con el SHA-256 del cuerpo en JSON canónico: modelo, mensajes, `max_tokens` y temperatura.
- La clave no lleva el token ni el `request_id`. Dos peticiones con el mismo cuerpo comparten la respuesta
  (`tests/test_gateway.py::test_the_cache_key_depends_on_the_request_body_and_not_on_the_request_id`).
- Con `CACHE_TTL_SECONDS=0` se manda `cf-aig-skip-cache: true`: ninguna respuesta sale de la caché.
- Un `HIT` (`cf-aig-cache-status`) cuesta 0 y cuenta en `cached_calls` y en `cache_hit_rate`, como desde la
  sesión 3.
- **Valores aceptados:** 0 (sin caché) o de 60 segundos en adelante, que es el mínimo de AI Gateway. Con 1 a 59 el
  producto no arranca (`app/config.py` · `_cache_ttl`).
- **Compliance:** producción no arranca con `CACHE_TTL_SECONDS` mayor que 900 (15 minutos). La regla está en
  `app/compliance.py` · `CEILINGS`.

**Ablación** (`scripts/cost_ablation.py` → `doc/evidence/cost-ablation.md`):

- Corre el set de evaluación de la fábrica con el modelo simulado y la aplicación en memoria.
- Mide la línea base, cada palanca sola, las dos juntas y dos configuraciones descartadas.
- Toma del juez los casos, el historial y la regla de calidad; no los copia. Una prueba comprueba que mide lo
  mismo que `verification evaluate` (`tests/test_cost_scripts.py`).

**Muestra con el modelo real** (`scripts/cost_real_sample.py` → `doc/evidence/cost-real-model.md`):

- La corre la persona con sus credenciales. Hace cuatro pasadas del set:
  1. Línea base.
  2. Palancas sin caché.
  3. Palancas con caché, para llenarla.
  4. La misma pasada repetida, que debe salir de la caché y costar 0.
- Si la segunda pasada no da `HIT`, el script lo reporta y termina con 1.

## Por qué la caché entra con 5 minutos

- **Solo responde a quien ya está autenticado.** El gateway exige su token (ADR 0008) y la API exige su clave.
- **No filtra datos entre clientes.** Una respuesta guardada solo se sirve para un cuerpo idéntico: misma
  pregunta, ya redactada (ADR 0014), mismo historial y mismo modelo. Quien la recibe mandó exactamente el mismo
  texto. Todos los clientes son personal de RH con el mismo acceso al directorio, así que compartir la caché no les
  da nada que no obtendrían del modelo.
- **El riesgo de datos viejos es bajo.** Cada pregunta hace dos llamadas al modelo:
  - La primera decide qué herramienta usar. Repetirla da la misma decisión: la temperatura es 0.
  - La segunda lleva en su cuerpo el resultado de la herramienta. Si el directorio cambia, cambia su clave y no
    sale de la caché.
  - Lo único que puede repetirse hasta 5 minutos es una respuesta mala: la misma pregunta exacta recibe la misma
    respuesta mala durante ese tiempo.
- **Ahorra costo, cuota y latencia en preguntas repetidas.** Una llamada desde la caché no gasta neuronas de
  Workers AI ni espera al modelo.

## Consecuencias

- Producción ya no depende de los valores por omisión del código. Un cambio de palanca pasa por el Blueprint,
  sus pruebas y compliance.
- Con el modelo simulado solo se miden el enrutamiento y la ventana:
  - −84.0 % de costo solo con el enrutamiento.
  - −14.7 % de costo y −23.6 % de tokens solo con la ventana.
  - −86.2 % con las dos.
- El tope y la caché se miden con el modelo real (`doc/cost-analysis.md`).
- **Trade-off de la caché:** costo y latencia frente a respuestas de hasta 5 minutos de antigüedad. Para apagarla
  basta `CACHE_TTL_SECONDS=0` en Render, sin desplegar código. Se vigila con `cache_hit_rate` y `cached_calls`.
- El set de evaluación de la fábrica solo tiene preguntas simples (las 8 van al modelo pequeño). Por eso no mide
  la calidad del enrutamiento en preguntas complejas; el análisis de costos lo registra como riesgo.
- Las cabeceras y los límites de la TTL coinciden con la documentación de AI Gateway (Caching y Custom metadata),
  revisada el 2026-11-26: la caché está apagada por omisión en el gateway y una petición entra con
  `cf-aig-cache-key`. La TTL va de 60 s a un mes. `cf-aig-metadata` acepta hasta 5 entradas y el producto manda
  una.
- La muestra real confirma el `HIT`. Si no llega, la palanca se apaga (`CACHE_TTL_SECONDS=0`) y este ADR se
  actualiza.
