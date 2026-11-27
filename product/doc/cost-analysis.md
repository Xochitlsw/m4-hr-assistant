# Análisis de costos del HR Assistant

Qué cuesta cada respuesta, cuánto ahorra cada palanca, qué riesgo de calidad tiene y cómo se vigila en
producción. Decisiones: ADR [0007](adr/0007-rule-based-routing-and-history-window.md),
[0008](adr/0008-gateway-model-cache-and-cost-accounting.md), [0017](adr/0017-request-origin-auth-throttling-and-budget.md)
y [0022](adr/0022-cost-levers-in-production-and-gateway-cache.md).

## 1. Precios y fecha

Fuente: <https://developers.cloudflare.com/workers-ai/platform/pricing/>. Se copiaron en
`data/model_prices.json` el 2026-11-04 y se **confirmaron contra la tabla vigente el 2026-11-26**.

| Modelo | Uso | Entrada, por millón de tokens | Salida, por millón de tokens |
| --- | --- | --- | --- |
| `@cf/meta/llama-3.1-8b-instruct-fast` | `MODEL_SMALL`, preguntas simples | 0.045 USD (4 119 neuronas) | 0.384 USD (34 868 neuronas) |
| `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | `MODEL_LARGE`, preguntas complejas y línea base | 0.293 USD (26 668 neuronas) | 2.253 USD (204 805 neuronas) |

- **Nombre del modelo pequeño.** La tabla vigente no lista `@cf/meta/llama-3.1-8b-instruct-fast`. Lista
  `@cf/meta/llama-3.1-8b-instruct-fp8-fast` al mismo precio. El producto conserva el nombre que usa, que responde
  por el gateway, y lo cobra con ese precio. Mejora futura: cambiar al nombre que lista la tabla.

- Costo de una llamada = (tokens de entrada × precio de entrada + tokens de salida × precio de salida) / 1 000 000
  (`app/pricing.py`).
- Una llamada que contesta la caché del gateway (`cf-aig-cache-status: HIT`) cuesta 0.
- **Neuronas.** Workers AI cobra en neuronas: 10 000 gratis al día, que se reinician a las 00:00 UTC. En Workers
  Paid, lo que pasa de ellas cuesta 0.011 USD por cada 1 000 neuronas. La asignación gratis equivale a unos
  0.11 USD diarios, y los precios por token de la tabla son su equivalente.
- **Consumo real.** `cost_usd` es la estimación por tokens; el consumo real se valida en el panel de Cloudflare
  (Workers AI, neuronas por día).

## 2. Cómo se mide

- **Línea base:** modelo grande para todo, historial completo y sin caché (`ROUTING=false`,
  `HISTORY_WINDOW=1000`, `CACHE_TTL_SECONDS=0`).
- **Producción:** las palancas del Blueprint (`render.yaml`, ADR 0022): enrutamiento, ventana de 6 mensajes, tope
  de 300 tokens, caché de 300 segundos y presupuesto diario de 1 USD por cliente.
- **Set:** `verification/eval_set.json`, 8 preguntas, cada una con 10 mensajes de historial de relleno (una
  conversación larga).
  - La calidad es la regla del juez: la respuesta contiene el dato esperado.
  - La regla para aceptar una palanca es la de la sesión 3: misma calidad o mejor, y menos costo.
- **Con el modelo simulado** (sin red ni costo) se miden el enrutamiento (el precio depende del nombre del
  modelo) y la ventana (tokens de entrada). Cuenta los tokens por palabras: los números sirven para comparar
  configuraciones, no como monto real.
- **Solo con el modelo real** se miden el tope de salida (el simulado no tiene tope) y la caché (está en el
  gateway). También ahí salen los tokens y la latencia reales.

| Evidencia | Comando (desde `product/`, salvo `evaluate`) | Archivo |
| --- | --- | --- |
| Línea base contra producción | `uv run python -m verification evaluate` (desde la raíz) | `doc/evidence/evaluate-output.txt` |
| Ablación por palanca | `uv run python scripts/cost_ablation.py` | `doc/evidence/cost-ablation.md` |
| Muestra con el modelo real | `uv run --env-file .env python scripts/cost_real_sample.py` | `doc/evidence/cost-real-model.md` |

## 3. Resultado con el modelo simulado

`verification evaluate`: calidad de 100 % en las dos configuraciones; costo de 0.001134 a 0.000156 USD
(−86.2 %); tokens de entrada de 2416 a 1846 (−23.6 %).

Ablación (`doc/evidence/cost-ablation.md`, 2026-11-26):

| Configuración | Calidad | Costo USD | Cambio en costo | Tokens de entrada | Cambio en tokens | Rutas |
| --- | --- | --- | --- | --- | --- | --- |
| Línea base: modelo grande, historial completo | 100 % | 0.001134 | — | 2416 | — | `routing_off` 8 |
| + ventana de 6 mensajes | 100 % | 0.000967 | −14.7 % | 1846 | −23.6 % | `routing_off` 8 |
| + enrutamiento por regla | 100 % | 0.000181 | −84.0 % | 2416 | 0 % | `simple` 8 |
| **Ambas (producción)** | **100 %** | **0.000156** | **−86.3 %** | **1846** | **−23.6 %** | `simple` 8 |
| Ventana 0, sin historial (descartada) | 100 % | 0.000117 | −89.7 % | 991 | −59.0 % | `simple` 8 |
| Todo al modelo pequeño (descartada) | 100 % | 0.000156 | −86.3 % | 1846 | −23.6 % | `routing_off` 8 |

Lectura:

- **El enrutamiento es la palanca que más ahorra.** El modelo pequeño cuesta unas 6.5 veces menos por token de
  entrada.
- **La ventana ahorra tokens.** Su efecto crece con conversaciones más largas.
- **Las dos se combinan:** una baja el precio por token y la otra baja los tokens.
- **«Todo al modelo pequeño» cuesta lo mismo que producción en este set**, porque sus 8 preguntas son simples y ya
  van al modelo pequeño. El set no tiene preguntas complejas (comparar, resumir), así que **no mide la calidad del
  enrutamiento en ellas**. Es un riesgo registrado (sección 6).
- **La ventana 0 parece mejor, pero no lo es.** El modelo simulado no lee el historial y el set no tiene preguntas
  de seguimiento, así que su calidad de 100 % no significa nada (sección 7).
- **El simulado predijo bien el ahorro:** −86.2 % frente a −85.9 % con el modelo real (sección 4). Los montos sí
  difieren: los tokens reales son unas 2.3 veces las palabras que cuenta el simulado.

## 4. Muestra con el modelo real (C2, C5)

La corre la persona con sus credenciales:

```powershell
uv run --env-file .env python scripts/cost_real_sample.py
```

- **Qué hace.** Corre el set cuatro veces con el modelo real: 32 preguntas y unas 64 llamadas, dentro de la cuota
  gratis.
  1. `baseline`: modelo grande, historial completo, sin caché.
  2. `levers`: palancas de producción, sin caché.
  3. `cache_fill`: las mismas palancas con caché de 300 s; llena la caché.
  4. `cache_repeat`: repite la pasada 3.
- **Qué escribe.** `doc/evidence/cost-real-model.md` con calidad, respuestas 200, costo, tokens de entrada y de
  salida, llamadas, llamadas desde la caché y latencia p50/p95 de cada pasada. También la proyección y una fila
  por caso, sin texto de preguntas ni respuestas.
- **Qué debe salir:**
  - Código 0.
  - 8/8 respuestas 200 en cada pasada.
  - `levers` con la misma calidad que `baseline` o mejor, y menos costo.
  - `cache_repeat` con todas sus llamadas desde la caché y costo 0.
  - El formato de respuesta de Cloudflare (`choices` y `usage`) queda confirmado si todas son 200 con tokens
    mayores que 0. Un formato distinto respondería 502.
- **Si algo falla,** la sección «Verificación» del archivo dice qué y el script termina con 1:
  - Si la caché no responde, se apaga (`CACHE_TTL_SECONDS=0`) y se actualiza el ADR 0022.
  - Si la calidad baja con el modelo pequeño, se revisa la regla del enrutador antes de aceptar el ahorro.

**Resultado** (2026-11-26, 21:08 UTC; `doc/evidence/cost-real-model.md`): código 0, sin problemas.

| Pasada | Calidad | Respuestas 200 | Costo USD | Cambio en costo | Tokens de entrada | Tokens de salida | Llamadas desde la caché | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Línea base: 70B, historial completo, sin caché | 100 % | 8/8 | 0.002646 | — | 5556 | 452 | 0 de 15 | 1707 | 2965 |
| **Palancas de producción, sin caché** | **100 %** | 8/8 | **0.000374** | **−85.9 %** | 4476 | 449 | 0 de 15 | 687 | 919 |
| Palancas y caché de 300 s, 1.ª pasada (la llena) | 100 % | 8/8 | 0.000376 | −85.8 % | 4476 | 455 | 0 de 15 | 910 | 999 |
| **Palancas y caché, 2.ª pasada (repite)** | **100 %** | 8/8 | **0.000000** | **−100 %** | 4476 | 455 | **15 de 15** | **87** | **99** |

Lectura:

- **Ahorro sin degradar.** Las palancas bajan el costo 85.9 % con la misma calidad (100 %): el modelo pequeño
  responde bien las 8 preguntas del set.
- **Ventana.** Los tokens de entrada bajan 19.4 % (de 5556 a 4476).
- **Latencia.** El modelo pequeño también es más rápido:
  - El p50 baja de 1707 a 687 ms (−60 %).
  - El p95 baja de 2965 a 919 ms. La línea base no cumple el SLO de 2000 ms; producción, sí.
- **Caché confirmada.** En la repetición, las 15 llamadas salieron de la caché (`HIT`), con costo 0 y p50 de 87 ms.
  - La clave por hash del cuerpo funciona aunque cada petición lleve su propio `request_id` en los metadatos.
  - La 1.ª pasada, que llena la caché, cuesta lo mismo que sin caché. En esta corrida su latencia fue algo mayor
    (p50 de 910 frente a 687 ms).
- **Tope de salida.** No se alcanzó: la petición con más salida usó 77 tokens en 2 llamadas, lejos de 300 por
  llamada. En este set el tope no ahorra ni corta; es una red de seguridad contra respuestas desbocadas.
- **Formato de Cloudflare confirmado.** Las 32 respuestas fueron 200, con `choices` y `usage`.
- **Variación a temperatura 0.** Entre la pasada sin caché y la 1.ª con caché, 2 de 8 casos cambiaron sus tokens
  de salida (por ejemplo, 54 frente a 63). La caché devuelve exactamente la respuesta guardada: durante 5 minutos,
  la misma pregunta exacta recibe la misma respuesta.

## 5. Proyección (C5)

Se calcula con el costo promedio por petición de la muestra real (sección 4) y 1 000 peticiones al día (30 000 al
mes), con los precios confirmados el 2026-11-26.

| Configuración | USD por 1 000 peticiones | USD al mes |
| --- | --- | --- |
| Línea base | 0.3308 | 9.92 |
| **Producción, sin aciertos de caché** | **0.0467** | **1.40** |
| Producción con 20 % de aciertos de caché | 0.0374 | 1.12 |
| Producción con todas las llamadas desde la caché (el techo) | 0.0000 | 0.00 |

La ablación con el modelo simulado (sección 3) reparte el ahorro entre las palancas. Sus montos (4.25 y 0.58 USD
al mes) solo sirven para comparar, porque cuentan palabras en lugar de tokens.

- **Caché:** con una tasa de aciertos `h`, el costo de producción baja a un factor (1 − h), aproximadamente. La
  tasa real se lee en `cache_hit_rate` de `/metrics`.
- **Asignación gratis:** a este ritmo, producción gasta unos 0.05 USD al día y cabe en las 10 000 neuronas
  diarias (unos 0.11 USD); sin pagar, alcanza hasta unas 2 350 peticiones al día. La línea base, con 0.33 USD al
  día, no cabría:

  | Configuración | Neuronas al día (1 000 peticiones) | USD al mes en Workers Paid, sin contar las 10 000 gratis |
  | --- | --- | --- |
  | Línea base | ~30 100 | ~6.62 |
  | Producción | ~4 250 | 0.00 |
- **Presupuesto por cliente:** 1 USD al día alcanza para unas 21 000 respuestas de producción, o 3 000 de línea
  base. Es un techo contra abuso o errores (OWASP LLM10), no un límite del uso normal; al pasarlo, la API responde
  `429` con `budget_exceeded`.

## 6. Trade-offs por palanca (C4)

| Palanca | Ahorro medido | Riesgo de calidad | Mitigación | Cómo se vigila | ¿Se queda? |
| --- | --- | --- | --- | --- | --- |
| 1. Enrutamiento por regla | −84.0 % de costo (simulado); con la ventana, −85.9 % real con la misma calidad | Una pregunta compleja que no parece compleja va al modelo pequeño y recibe una respuesta pobre | El modelo grande atiende las preguntas largas o con «compara», «resume», «explica», «por qué», «analiza» o «todos». El fallback al otro modelo cubre las caídas (ADR 0018). En la muestra real, el 8B respondió bien las 8 preguntas, igual que el 70B | `by_route` y `by_model` en `/metrics`; `route` y `model` en las trazas; `verification evaluate` en cada push (trabajo `factory`) | Sí |
| 2. Ventana de 6 mensajes | −14.7 % de costo y −23.6 % de tokens (simulado); −19.4 % de tokens reales; crece con conversaciones largas | El modelo no ve lo que se dijo hace más de 3 turnos | 3 turnos cubren las preguntas de seguimiento normales; la página manda los últimos 20 mensajes y el servidor elige cuántos llegan al modelo; se cambia con `HISTORY_WINDOW` | `input_tokens` por modelo en `/metrics`; tokens por llamada en LangSmith | Sí |
| 3. Tope de salida de 300 tokens | No ahorra en este set: la salida máxima fue de 77 tokens por petición (muestra real); es una red de seguridad | Una respuesta larga, como la lista de un departamento grande, se corta y el JSON del protocolo queda incompleto | 300 tokens cubren un empleado o un departamento de este directorio; compliance no deja pasar de 1024 en producción | `output_tokens` por modelo: un promedio cerca de 300 por llamada indica cortes; `errors_by_type` y `stopped_at_step_cap` | Sí |
| 4. Caché del gateway, 300 s | Muestra real: en la repetición, 15 de 15 llamadas desde la caché, costo 0 y p50 de 87 ms (frente a 687 ms) | Una respuesta de hasta 5 minutos de antigüedad; una respuesta mala se repite para la misma pregunta exacta | La clave es el hash del cuerpo, que incluye el resultado de la herramienta: si el directorio cambia, la respuesta no sale de la caché. Temperatura 0. Compliance pone un máximo de 900 s. Se apaga con `CACHE_TTL_SECONDS=0` sin desplegar código | `cache_hit_rate` en `/metrics`; `cached_calls` por respuesta y en la traza; registros del AI Gateway | Sí; confirmada en la muestra real |
| 5. Presupuesto diario por cliente, 1 USD | No ahorra: pone un techo | Un cliente legítimo con mucho uso recibe `429` hasta el día siguiente | Se sube con `CLIENT_DAILY_BUDGET_USD`; `Retry-After` dice cuándo volver | `by_client.*.cost_usd` y `budget_exceeded` en `/metrics`; auditoría `budget_exceeded` en el log | Sí |

## 7. La palanca que no aceptaría

**La ventana 0 (no mandar historial).** En la ablación es la que más ahorra (−89.7 % frente a −86.3 %) con
calidad de 100 %. El número engaña: el modelo simulado no lee el historial y el set no tiene preguntas de
seguimiento. En una conversación real, «¿y en qué año ingresó?» después de preguntar por EMP-0004 ya no tendría a
quién referirse. Se ganarían 3.4 puntos de ahorro y se rompería la conversación sin que ninguna métrica lo
mostrara. No se acepta un ahorro que la medición no puede ver.

Por la misma razón tampoco se aceptan:

- **Todo al modelo pequeño.** En este set no ahorra nada más y quita el modelo grande de las comparaciones y los
  resúmenes.
- **Un tope de salida muy bajo,** por ejemplo 64. Corta el JSON de una lista de empleados y la respuesta falla.

## 8. Palancas descartadas y por qué

| Palanca | Por qué no |
| --- | --- |
| Ventana 0, todo al modelo pequeño, tope muy bajo | Sección 7: ahorran solo en lo que el set no mide |
| Caché propia de la aplicación (por cliente, pregunta e historial) | Duplica la del gateway y agrega estado a una API sin estado (ADR 0001). Guardaría en el servicio preguntas que pueden traer datos personales |
| Compactar los resultados de las herramientas; un prompt de sistema más corto | No se midieron. La regla es que cada palanca se mide o no se usa. Quedan como trabajo futuro, con la ablación como medida |
| Atajo determinista (contestar con el resultado de la herramienta, sin la segunda llamada) | Ahorraría cerca de la mitad de las llamadas, pero saca al modelo de la respuesta y cambia su estilo. No se midió |

## 9. Cómo se vigila el costo en producción

- **`/metrics` y `/dashboard`:**
  - `cost_usd` total y `cost_per_answer_usd`.
  - `by_model`: respuestas, tokens y costo.
  - `by_route` y `by_client`: costo, `429` por límite y por presupuesto.
  - `cache_hit_rate`.
- **LangSmith:**
  - Cada traza `ask` lleva `cost_usd`, `model`, `route` y `cached_calls`.
  - Cada llamada lleva `usage_metadata`.
  - Gráficas de costo por modelo y por ruta (`doc/observability.md` §4).
- **Alertas y runbook** (`doc/observability.md`):
  - §6, umbrales: el costo del día pasa la suma de los presupuestos, un cliente con `budget_exceeded`, o
    `cache_hit_rate` en 0 con la caché encendida.
  - §5, receta «¿Por qué subió el costo?».
- **Regresiones:** el trabajo `factory` del CI corre `verification evaluate` en cada push. Un cambio que suba el
  costo o baje la calidad frente a la línea base deja el CI en rojo, y Render no despliega (`checksPass`).
