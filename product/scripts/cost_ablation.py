"""Ablación de las palancas de costo con el modelo simulado (C3, ADR 0022).

Uso, desde ``product/`` (sin credenciales ni red):

    uv run python scripts/cost_ablation.py

Corre el set de evaluación de la fábrica (``verification/eval_set.json``) contra la
aplicación en memoria, una vez por configuración: la línea base, cada palanca sola, las
dos juntas (producción) y dos configuraciones descartadas. Usa los mismos casos, el
mismo historial de relleno y la misma regla de calidad que ``verification evaluate``
(los importa del juez; no los copia). Escribe ``doc/evidence/cost-ablation.md``.

Con el modelo simulado solo se miden el enrutamiento (precio por modelo) y la ventana
(tokens de entrada): el modelo simulado cuenta tokens por palabras y no tiene tope de
salida ni caché. El tope y la caché se miden con ``scripts/cost_real_sample.py``.
"""

import io
import json
import secrets
import statistics
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

PRODUCT = Path(__file__).resolve().parents[1]
ROOT = PRODUCT.parent
OUTPUT = PRODUCT / "doc" / "evidence" / "cost-ablation.md"
PRICES = PRODUCT / "data" / "model_prices.json"
SMALL = "@cf/meta/llama-3.1-8b-instruct-fast"
REQUESTS_PER_DAY = 1000
DAYS_PER_MONTH = 30
# Línea base del módulo: modelo grande, historial completo y sin caché.
BASELINE = {"ROUTING": "false", "HISTORY_WINDOW": "1000"}
FAKE_ENV = {
    "MODEL_PROVIDER": "fake",
    "ENVIRONMENT": "development",
    "LANGSMITH_TRACING": "false",
    "RATE_LIMIT_PER_MINUTE": "1000",
}
# El producto va al frente; la raíz (de donde sale ``verification``) va al final,
# para que su paquete ``tests`` no tape al del producto.
if str(PRODUCT) not in sys.path:
    sys.path.insert(0, str(PRODUCT))
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


class Config(NamedTuple):
    """Una configuración de la ablación.

    :param name: Nombre corto (llave de los resultados)
    :param label: Nombre para la tabla
    :param env: Variables que cambia frente a los valores por omisión
    :param kept: ``False`` si es una palanca descartada
    """

    name: str
    label: str
    env: dict
    kept: bool = True


CONFIGS = (
    Config("baseline", "Línea base: modelo grande, historial completo", BASELINE),
    Config("window", "+ ventana de 6 mensajes", {**BASELINE, "HISTORY_WINDOW": "6"}),
    Config("routing", "+ enrutamiento por regla", {**BASELINE, "ROUTING": "true"}),
    Config("production", "Ambas (producción)", {}),
    Config("window_0", "Ventana 0, sin historial (descartada)", {"HISTORY_WINDOW": "0"},
           kept=False),
    Config("all_small", "Todo al modelo pequeño (descartada)",
           {"ROUTING": "false", "MODEL_LARGE": SMALL}, kept=False),
)


class Judge(NamedTuple):
    """Lo que la ablación toma de ``verification/evaluate.py``."""

    cases: list
    history: list
    is_correct: Callable[[str, dict], bool]
    measure: Callable


@dataclass
class Result:  # pylint: disable=too-many-instance-attributes
    """Totales de una corrida del set de evaluación."""

    requests: int = 0
    ok: int = 0
    correct: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    cached_calls: int = 0
    routes: dict = field(default_factory=dict)
    latencies_ms: list = field(default_factory=list)
    rows: list = field(default_factory=list)

    @property
    def quality(self) -> float:
        """Fracción de casos correctos."""
        return self.correct / self.requests if self.requests else 0.0

    def latency(self, quantile: float) -> float:
        """Latencia en ms del cuantil pedido (0.5 o 0.95)."""
        if len(self.latencies_ms) < 2:
            return self.latencies_ms[0] if self.latencies_ms else 0.0
        cuts = statistics.quantiles(self.latencies_ms, n=100, method="inclusive")
        return cuts[round(quantile * 100) - 1]


def judge() -> Judge:
    """Casos, historial y reglas del juez de la fábrica (sin copiarlos)."""
    # pylint: disable=import-outside-toplevel
    from verification import evaluate

    cases = json.loads(evaluate.EVAL_SET.read_text(encoding="utf-8"))
    return Judge(cases, evaluate.HISTORY, evaluate.is_correct, evaluate.measure)


@contextmanager
def app_client(env: dict, http_client=None):
    """La aplicación en memoria con su propia clave y un log que no se imprime.

    :param env: Configuración completa (no se lee el entorno del proceso)
    :param http_client: Cliente HTTP del modelo real (``None`` con ``fake``)
    """
    # pylint: disable=import-outside-toplevel
    from fastapi.testclient import TestClient

    from app.clock import SystemClock
    from app.config import load_settings
    from app.logs import EventLogger
    from app.main import Observability, create_app
    from app.metrics import Metrics
    from app.tracing import Tracer

    key = secrets.token_urlsafe(24)
    settings = load_settings({**env, "API_KEYS": f"cost-check:{key}"})
    metrics = Metrics(settings.observability.slo_p95_ms)
    logger = EventLogger(
        "hr-assistant", SystemClock(), io.StringIO(), observer=metrics.observe
    )
    observability = Observability(
        clock=SystemClock(), logger=logger, metrics=metrics, tracer=Tracer()
    )
    app = create_app(
        settings=settings, observability=observability, http_client=http_client
    )
    with TestClient(app, headers={"X-API-Key": key}) as client:
        yield client


def measure(client, rules: Judge, clock=time.perf_counter) -> Result:
    """Pregunta cada caso (con el historial del juez) y suma calidad y costo.

    :return: Totales y una fila por caso, sin texto de pregunta ni de respuesta
    :rtype: Result
    """
    result = Result()
    routes = Counter()
    for number, case in enumerate(rules.cases, start=1):
        started = clock()
        response = client.post(
            "/ask", json={"question": case["question"], "history": rules.history}
        )
        latency = round((clock() - started) * 1000, 1)
        body = response.json() if response.status_code == 200 else {}
        correct = bool(body) and rules.is_correct(body["answer"], case)
        row = {
            "case": number,
            "status": response.status_code,
            "correct": correct,
            "model": body.get("model", "-"),
            "route": body.get("route", "-"),
            "input_tokens": body.get("input_tokens", 0),
            "output_tokens": body.get("output_tokens", 0),
            "steps": body.get("steps", 0),
            "cached_calls": body.get("cached_calls", 0),
            "cost_usd": body.get("cost_usd", 0.0),
            "latency_ms": latency,
        }
        result.rows.append(row)
        result.requests += 1
        result.ok += response.status_code == 200
        result.correct += correct
        result.cost_usd += row["cost_usd"]
        result.input_tokens += row["input_tokens"]
        result.output_tokens += row["output_tokens"]
        result.model_calls += row["steps"]
        result.cached_calls += row["cached_calls"]
        result.latencies_ms.append(latency)
        routes[row["route"]] += 1
    result.routes = dict(routes)
    return result


def run_ablation(base: dict | None = None) -> list[tuple[Config, Result]]:
    """Mide cada configuración de :data:`CONFIGS` con el modelo simulado."""
    rules = judge()
    results = []
    for config in CONFIGS:
        with app_client({**FAKE_ENV, **(base or {}), **config.env}) as client:
            results.append((config, measure(client, rules)))
    return results


def per_thousand(result: Result) -> float:
    """Costo en USD por 1 000 peticiones."""
    return result.cost_usd / result.requests * 1000 if result.requests else 0.0


def per_month(result: Result) -> float:
    """Costo en USD al mes con 1 000 peticiones al día."""
    return per_thousand(result) / 1000 * REQUESTS_PER_DAY * DAYS_PER_MONTH


def change(value: float, baseline: float) -> str:
    """Cambio porcentual frente a la línea base, con signo."""
    if not baseline:
        return "-"
    return f"{(value - baseline) / baseline:+.1%}".replace("%", " %")


def prices_note() -> str:
    """Precios usados y su fecha (``data/model_prices.json``)."""
    data = json.loads(PRICES.read_text(encoding="utf-8"))
    lines = [f"Precios en USD por millón de tokens, revisados: {data['checked']}."]
    for name, price in data["models"].items():
        if name != "fake":
            lines.append(f"- `{name}`: entrada {price['input']}, salida {price['output']}")
    return "\n".join(lines)


def render(results: list[tuple[Config, Result]]) -> str:
    """La tabla de la ablación, la proyección y la nota de método, en Markdown."""
    base = results[0][1]
    now = datetime.now(timezone.utc)
    lines = [
        "# Ablación de las palancas de costo (modelo simulado)",
        "",
        f"Generado: {now:%Y-%m-%d %H:%M} UTC con `uv run python scripts/cost_ablation.py`.",
        f"Set de evaluación de la fábrica: {base.requests} preguntas, cada una con 10",
        "mensajes de historial de relleno (los mismos de `verification evaluate`).",
        "",
        prices_note(),
        "",
        "| Configuración | Calidad | Costo USD | Cambio en costo | Tokens de entrada | "
        "Cambio en tokens | Rutas | USD por 1 000 peticiones | USD al mes (1 000/día) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for config, result in results:
        routes = ", ".join(f"{name} {count}" for name, count in result.routes.items())
        lines.append(
            f"| {config.label} | {result.quality:.0%} | {result.cost_usd:.6f} | "
            f"{change(result.cost_usd, base.cost_usd)} | {result.input_tokens} | "
            f"{change(result.input_tokens, base.input_tokens)} | {routes} | "
            f"{per_thousand(result):.4f} | {per_month(result):.2f} |"
        )
    lines += [
        "",
        "- Las filas «descartada» ahorran lo mismo o más con este set, pero no se usan: el",
        "  set solo tiene preguntas simples (todas van ya al modelo pequeño) y el modelo",
        "  simulado no lee el historial ni razona, así que su calidad aquí no mide la",
        "  pérdida real (ver `doc/cost-analysis.md`).",
        "- Tokens contados por palabras: la proyección es relativa. Los números",
        "  absolutos salen de la muestra con el modelo real",
        "  (`doc/evidence/cost-real-model.md`).",
        "- Sin texto de preguntas ni respuestas: solo configuración y totales.",
    ]
    return "\n".join(lines) + "\n"


def main(output: Path = OUTPUT) -> int:
    """Corre la ablación, escribe la evidencia y aplica la regla del juez.

    :return: 0 si producción tiene la misma calidad o mejor y cuesta menos que la
        línea base; 1 si no
    :rtype: int
    """
    results = run_ablation()
    report = render(results)
    output.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Evidencia: {output.relative_to(output.parents[2]).as_posix()}")
    named = {config.name: result for config, result in results}
    baseline, production = named["baseline"], named["production"]
    passed = (
        production.quality >= baseline.quality
        and production.cost_usd < baseline.cost_usd
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
