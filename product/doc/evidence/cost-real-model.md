# Muestra de costos con el modelo real (Workers AI vía AI Gateway)

Generado: 2026-11-26 21:08 UTC con
`uv run --env-file .env python scripts/cost_real_sample.py` (equipo local).
Set de evaluación de la fábrica: 8 preguntas con 10 mensajes de historial de
relleno, la misma regla de calidad que `verification evaluate`. Los casos se
numeran en el orden de `verification/eval_set.json`.

Precios en USD por millón de tokens, revisados: 2026-11-26, contra la tabla vigente.
- `@cf/meta/llama-3.1-8b-instruct-fast`: entrada 0.045, salida 0.384
- `@cf/meta/llama-3.3-70b-instruct-fp8-fast`: entrada 0.293, salida 2.253

## Resumen

| Pasada | Calidad | Respuestas 200 | Costo USD | Cambio en costo | Tokens entrada | Tokens salida | Llamadas | Desde caché | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `baseline`: Línea base: modelo grande, historial completo, sin caché | 100% | 8/8 | 0.002646 | +0.0 % | 5556 | 452 | 15 | 0 | 1707 | 2965 |
| `levers`: Palancas de producción, sin caché | 100% | 8/8 | 0.000374 | -85.9 % | 4476 | 449 | 15 | 0 | 687 | 919 |
| `cache_fill`: Palancas y caché de 300 s, 1.ª pasada (llena la caché) | 100% | 8/8 | 0.000376 | -85.8 % | 4476 | 455 | 15 | 0 | 910 | 999 |
| `cache_repeat`: Palancas y caché de 300 s, 2.ª pasada (repite) | 100% | 8/8 | 0.000000 | -100.0 % | 4476 | 455 | 15 | 15 | 87 | 99 |

## Proyección

Con el costo promedio por petición de cada pasada. `cache_repeat` es el techo
del ahorro (todas las llamadas desde la caché); en producción el ahorro real
es proporcional a `cache_hit_rate` de `/metrics`.

| Configuración | USD por 1 000 peticiones | USD al mes (1 000/día) |
| --- | --- | --- |
| `baseline` | 0.3308 | 9.92 |
| `levers` | 0.0467 | 1.40 |
| `cache_repeat` | 0.0000 | 0.00 |

## Verificación

Sin problemas: respuestas 200, misma calidad o mejor y menos costo con las
palancas, y la caché contestó la segunda pasada.

## Casos

| Pasada | Caso | Código | Correcta | Modelo | Ruta | Entrada | Salida | Llamadas | Caché | Costo USD | ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `baseline` | 1 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 737 | 73 | 2 | 0 | 0.00038041 | 3204 |
| `baseline` | 2 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 735 | 54 | 2 | 0 | 0.00033702 | 1552 |
| `baseline` | 3 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 745 | 72 | 2 | 0 | 0.0003805 | 2522 |
| `baseline` | 4 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 835 | 77 | 2 | 0 | 0.00041814 | 1895 |
| `baseline` | 5 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 749 | 42 | 2 | 0 | 0.00031408 | 1684 |
| `baseline` | 6 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 739 | 49 | 2 | 0 | 0.00032692 | 1656 |
| `baseline` | 7 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 692 | 49 | 2 | 0 | 0.00031315 | 1730 |
| `baseline` | 8 | 200 | sí | @cf/meta/llama-3.3-70b-instruct-fp8-fast | routing_off | 324 | 36 | 1 | 0 | 0.00017604 | 1001 |
| `levers` | 1 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 593 | 70 | 2 | 0 | 5.356e-05 | 973 |
| `levers` | 2 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 591 | 54 | 2 | 0 | 4.733e-05 | 624 |
| `levers` | 3 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 601 | 54 | 2 | 0 | 4.778e-05 | 697 |
| `levers` | 4 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 691 | 70 | 2 | 0 | 5.798e-05 | 678 |
| `levers` | 5 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 605 | 51 | 2 | 0 | 4.681e-05 | 818 |
| `levers` | 6 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 595 | 40 | 2 | 0 | 4.214e-05 | 595 |
| `levers` | 7 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 548 | 38 | 2 | 0 | 3.925e-05 | 748 |
| `levers` | 8 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 252 | 72 | 1 | 0 | 3.899e-05 | 578 |
| `cache_fill` | 1 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 593 | 70 | 2 | 0 | 5.356e-05 | 948 |
| `cache_fill` | 2 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 591 | 63 | 2 | 0 | 5.079e-05 | 1027 |
| `cache_fill` | 3 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 601 | 54 | 2 | 0 | 4.778e-05 | 929 |
| `cache_fill` | 4 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 691 | 70 | 2 | 0 | 5.798e-05 | 890 |
| `cache_fill` | 5 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 605 | 48 | 2 | 0 | 4.566e-05 | 930 |
| `cache_fill` | 6 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 595 | 40 | 2 | 0 | 4.214e-05 | 770 |
| `cache_fill` | 7 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 548 | 38 | 2 | 0 | 3.925e-05 | 890 |
| `cache_fill` | 8 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 252 | 72 | 1 | 0 | 3.899e-05 | 687 |
| `cache_repeat` | 1 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 593 | 70 | 2 | 2 | 0.0 | 90 |
| `cache_repeat` | 2 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 591 | 63 | 2 | 2 | 0.0 | 84 |
| `cache_repeat` | 3 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 601 | 54 | 2 | 2 | 0.0 | 100 |
| `cache_repeat` | 4 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 691 | 70 | 2 | 2 | 0.0 | 87 |
| `cache_repeat` | 5 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 605 | 48 | 2 | 2 | 0.0 | 86 |
| `cache_repeat` | 6 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 595 | 40 | 2 | 2 | 0.0 | 96 |
| `cache_repeat` | 7 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 548 | 38 | 2 | 2 | 0.0 | 80 |
| `cache_repeat` | 8 | 200 | sí | @cf/meta/llama-3.1-8b-instruct-fast | simple | 252 | 72 | 1 | 1 | 0.0 | 53 |

Sin texto de preguntas ni respuestas, sin el token ni la cuenta.
