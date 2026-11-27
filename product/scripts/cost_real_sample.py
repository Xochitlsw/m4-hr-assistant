"""Muestra de costos con el modelo real: línea base, palancas y caché (C2, C5).

Uso, desde ``product/``, con tus credenciales de Cloudflare en ``.env`` (el script
nunca imprime sus valores):

    uv run --env-file .env python scripts/cost_real_sample.py

Dentro de este proceso, y solo aquí, usa el modelo real (``MODEL_PROVIDER=gateway``),
el entorno de desarrollo, las trazas apagadas y una clave de cliente aleatoria que no
se guarda ni se muestra. Corre el set de evaluación de la fábrica cuatro veces (32
preguntas, unas 64 llamadas al modelo; cabe de sobra en la cuota gratis diaria):

1. ``baseline``: modelo grande, historial completo y sin caché.
2. ``levers``: palancas de producción (enrutamiento, ventana 6, tope 300), sin caché.
3. ``cache_fill``: las mismas palancas con la caché del gateway (TTL 300 s): la llena.
4. ``cache_repeat``: repite la pasada 3; la caché debe contestar y costar 0.

Escribe ``doc/evidence/cost-real-model.md``: calidad, costo, tokens, llamadas desde la
caché, latencia y la proyección mensual. Ningún texto de pregunta o respuesta, ni el
token, ni la cuenta.
"""

import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
import cost_ablation as ablation  # noqa: E402

OUTPUT = ablation.PRODUCT / "doc" / "evidence" / "cost-real-model.md"
CREDENTIALS = ("CF_ACCOUNT_ID", "CF_API_TOKEN")
LEVERS = {
    "ROUTING": "true",
    "HISTORY_WINDOW": "6",
    "MODEL_MAX_TOKENS": "300",
}
PASSES = (
    (
        "baseline",
        "Línea base: modelo grande, historial completo, sin caché",
        {**ablation.BASELINE, "CACHE_TTL_SECONDS": "0"},
    ),
    ("levers", "Palancas de producción, sin caché", {**LEVERS, "CACHE_TTL_SECONDS": "0"}),
    (
        "cache_fill",
        "Palancas y caché de 300 s, 1.ª pasada (llena la caché)",
        {**LEVERS, "CACHE_TTL_SECONDS": "300"},
    ),
    (
        "cache_repeat",
        "Palancas y caché de 300 s, 2.ª pasada (repite)",
        {**LEVERS, "CACHE_TTL_SECONDS": "300"},
    ),
)


def base_env(environ: Mapping[str, str]) -> dict:
    """Tu entorno con lo que este proceso cambia (tu ``.env`` no cambia).

    :param environ: Variables del proceso (las de ``.env`` con ``--env-file``)
    :return: Variables para armar la aplicación
    :rtype: dict
    """
    return {
        **environ,
        "MODEL_PROVIDER": "gateway",
        "ENVIRONMENT": "development",
        "LANGSMITH_TRACING": "false",
        "RATE_LIMIT_PER_MINUTE": "1000",
        "CLIENT_DAILY_BUDGET_USD": "5",
    }


def run_sample(base: dict, http_client) -> list[tuple[str, str, ablation.Result]]:
    """Corre las cuatro pasadas con un solo cliente HTTP (se crea una vez).

    :param base: Variables de :func:`base_env`
    :param http_client: Cliente HTTP hacia el AI Gateway
    :return: ``(nombre, etiqueta, resultado)`` por pasada
    """
    rules = ablation.judge()
    passes = []
    for name, label, env in PASSES:
        with ablation.app_client({**base, **env}, http_client) as client:
            passes.append((name, label, ablation.measure(client, rules)))
    return passes


def problems(results: Mapping[str, ablation.Result]) -> list[str]:
    """Lo que la muestra no cumplió; vacío si todo salió bien."""
    found = [
        f"{name}: {result.requests - result.ok} respuestas con error"
        for name, result in results.items()
        if result.ok < result.requests
    ]
    baseline, levers = results.get("baseline"), results.get("levers")
    if baseline and levers:
        if levers.quality < baseline.quality:
            found.append("las palancas bajaron la calidad frente a la línea base")
        if levers.cost_usd >= baseline.cost_usd:
            found.append("las palancas no bajaron el costo frente a la línea base")
    repeat = results.get("cache_repeat")
    if repeat and repeat.cached_calls == 0:
        found.append("la caché no respondió en la segunda pasada")
    return found


def _summary(passes) -> list[str]:
    base = passes[0][2]
    lines = [
        "| Pasada | Calidad | Respuestas 200 | Costo USD | Cambio en costo | Tokens entrada | "
        "Tokens salida | Llamadas | Desde caché | p50 ms | p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, label, result in passes:
        lines.append(
            f"| `{name}`: {label} | {result.quality:.0%} | {result.ok}/{result.requests} | "
            f"{result.cost_usd:.6f} | {ablation.change(result.cost_usd, base.cost_usd)} | "
            f"{result.input_tokens} | {result.output_tokens} | {result.model_calls} | "
            f"{result.cached_calls} | {result.latency(0.5):.0f} | "
            f"{result.latency(0.95):.0f} |"
        )
    return lines


def _projection(results: Mapping[str, ablation.Result]) -> list[str]:
    lines = [
        "| Configuración | USD por 1 000 peticiones | USD al mes (1 000/día) |",
        "| --- | --- | --- |",
    ]
    for name in ("baseline", "levers", "cache_repeat"):
        result = results[name]
        lines.append(
            f"| `{name}` | {ablation.per_thousand(result):.4f} | "
            f"{ablation.per_month(result):.2f} |"
        )
    return lines


def _cases(passes) -> list[str]:
    lines = [
        "| Pasada | Caso | Código | Correcta | Modelo | Ruta | Entrada | Salida | "
        "Llamadas | Caché | Costo USD | ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, _label, result in passes:
        for row in result.rows:
            lines.append(
                f"| `{name}` | {row['case']} | {row['status']} | "
                f"{'sí' if row['correct'] else 'no'} | {row['model']} | {row['route']} | "
                f"{row['input_tokens']} | {row['output_tokens']} | {row['steps']} | "
                f"{row['cached_calls']} | {row['cost_usd']} | {row['latency_ms']:.0f} |"
            )
    return lines


def render(passes) -> str:
    """La evidencia en Markdown: resumen, proyección, verificación y casos."""
    results = {name: result for name, _label, result in passes}
    found = problems(results)
    now = datetime.now(timezone.utc)
    lines = [
        "# Muestra de costos con el modelo real (Workers AI vía AI Gateway)",
        "",
        f"Generado: {now:%Y-%m-%d %H:%M} UTC con",
        "`uv run --env-file .env python scripts/cost_real_sample.py` (equipo local).",
        "Set de evaluación de la fábrica: 8 preguntas con 10 mensajes de historial de",
        "relleno, la misma regla de calidad que `verification evaluate`. Los casos se",
        "numeran en el orden de `verification/eval_set.json`.",
        "",
        ablation.prices_note(),
        "",
        "## Resumen",
        "",
        *_summary(passes),
        "",
        "## Proyección",
        "",
        "Con el costo promedio por petición de cada pasada. `cache_repeat` es el techo",
        "del ahorro (todas las llamadas desde la caché); en producción el ahorro real",
        "es proporcional a `cache_hit_rate` de `/metrics`.",
        "",
        *_projection(results),
        "",
        "## Verificación",
        "",
        "Sin problemas: respuestas 200, misma calidad o mejor y menos costo con las",
        "palancas, y la caché contestó la segunda pasada."
        if not found
        else "Problemas: " + "; ".join(found) + ".",
        "",
        "## Casos",
        "",
        *_cases(passes),
        "",
        "Sin texto de preguntas ni respuestas, sin el token ni la cuenta.",
    ]
    return "\n".join(lines) + "\n"


def main(environ: Mapping[str, str] | None = None, output: Path = OUTPUT) -> int:
    """Corre la muestra con el modelo real y escribe la evidencia.

    :return: 0 si no hubo problemas; 1 si hubo alguno (la evidencia dice cuál); 2 si
        faltan credenciales de Cloudflare
    :rtype: int
    """
    environ = os.environ if environ is None else environ
    missing = [name for name in CREDENTIALS if not environ.get(name, "").strip()]
    if missing:
        print(
            f"Faltan {', '.join(missing)}: corre con uv run --env-file .env ... "
            "desde product/"
        )
        return 2
    # pylint: disable=import-outside-toplevel,import-error
    from app.config import load_settings
    from app.gateway import build_http_client

    base = base_env(environ)
    with build_http_client(load_settings(base).models) as http_client:
        passes = run_sample(base, http_client)
    report = render(passes)
    output.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print("Evidencia: doc/evidence/cost-real-model.md")
    return 0 if not problems({name: result for name, _l, result in passes}) else 1


if __name__ == "__main__":
    sys.exit(main())
