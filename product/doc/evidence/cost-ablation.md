# Ablación de las palancas de costo (modelo simulado)

Generado: 2026-11-26 19:55 UTC con `uv run python scripts/cost_ablation.py`.
Set de evaluación de la fábrica: 8 preguntas, cada una con 10
mensajes de historial de relleno (los mismos de `verification evaluate`).

Precios en USD por millón de tokens, revisados: 2026-11-04 (verify before class: prices change).
- `@cf/meta/llama-3.1-8b-instruct-fast`: entrada 0.045, salida 0.384
- `@cf/meta/llama-3.3-70b-instruct-fp8-fast`: entrada 0.293, salida 2.253

| Configuración | Calidad | Costo USD | Cambio en costo | Tokens de entrada | Cambio en tokens | Rutas | USD por 1 000 peticiones | USD al mes (1 000/día) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Línea base: modelo grande, historial completo | 100% | 0.001134 | +0.0 % | 2416 | +0.0 % | routing_off 8 | 0.1417 | 4.25 |
| + ventana de 6 mensajes | 100% | 0.000967 | -14.7 % | 1846 | -23.6 % | routing_off 8 | 0.1208 | 3.63 |
| + enrutamiento por regla | 100% | 0.000181 | -84.0 % | 2416 | +0.0 % | simple 8 | 0.0227 | 0.68 |
| Ambas (producción) | 100% | 0.000156 | -86.3 % | 1846 | -23.6 % | simple 8 | 0.0195 | 0.58 |
| Ventana 0, sin historial (descartada) | 100% | 0.000117 | -89.7 % | 991 | -59.0 % | simple 8 | 0.0146 | 0.44 |
| Todo al modelo pequeño (descartada) | 100% | 0.000156 | -86.3 % | 1846 | -23.6 % | routing_off 8 | 0.0195 | 0.58 |

- Las filas «descartada» ahorran lo mismo o más con este set, pero no se usan: el
  set solo tiene preguntas simples (todas van ya al modelo pequeño) y el modelo
  simulado no lee el historial ni razona, así que su calidad aquí no mide la
  pérdida real (ver `doc/cost-analysis.md`).
- Tokens contados por palabras: la proyección es relativa. Los números
  absolutos salen de la muestra con el modelo real
  (`doc/evidence/cost-real-model.md`).
- Sin texto de preguntas ni respuestas: solo configuración y totales.
