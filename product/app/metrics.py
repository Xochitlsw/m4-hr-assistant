"""Métricas en memoria de este proceso, su SLO y sus desgloses (ADR 0005 y 0021).

Los campos del contrato (``requests``, ``error_rate``, ``latency_ms`` con p50 y p95,
``tokens``, ``slo`` y ``cost_usd``) no cambian. Los desgloses salen del log: el
:class:`app.logs.EventLogger` le pasa cada registro a :meth:`Metrics.observe`, así que
``/metrics`` y el log cuentan lo mismo. Todo es por proceso.
"""

import math
import os
import threading
from collections import Counter, deque
from collections.abc import Iterable, Sequence

from app.logs import WARNING_ACTIONS

EXCLUDED_PATHS = frozenset({"/health", "/ready", "/metrics", "/dashboard"})
LATENCY_WINDOW = 1000
COST_DECIMALS = 8
RATE_DECIMALS = 4
# Acciones de auditoría que se cuentan en «security»: los rechazos del log (sin el
# arranque negado, que no es tráfico) más las redacciones y limpiezas.
SECURITY_ACTIONS = (WARNING_ACTIONS - {"startup_refused"}) | {
    "pii_detected",
    "output_sanitized",
}
# Acciones por cliente que se cuentan en «by_client».
CLIENT_ACTIONS = ("rate_limited", "budget_exceeded")


def nearest_rank(values: Sequence[float], percent: float) -> float:
    """Percentil por rango más cercano.

    :param values: Valores, en cualquier orden
    :type values: Sequence[float]
    :param percent: Percentil entre 0 y 100
    :type percent: float
    :return: El valor del percentil, o 0.0 si no hay valores
    :rtype: float
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(percent / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


def _ratio(part: float, whole: float, decimals: int = RATE_DECIMALS) -> float:
    """``part / whole`` redondeado, o 0 si ``whole`` es 0."""
    return round(part / whole, decimals) if whole else 0.0


class Breakdown:
    """Desgloses por código, modelo, ruta y cliente, y contadores de negocio.

    No tiene candado propio: :class:`Metrics` lo usa siempre dentro del suyo.
    """

    def __init__(self):
        self.statuses: Counter = Counter()
        self.models: dict[str, Counter] = {}
        self.routes: Counter = Counter()
        self.clients: dict[str, Counter] = {}
        self.tools: Counter = Counter()
        self.events: Counter = Counter()
        self.answers: Counter = Counter()

    def add_answer(self, record: dict) -> None:
        """Suma un evento ``agent_run``."""
        model = self.models.setdefault(record.get("model", "?"), Counter())
        model.update(
            answers=1,
            input_tokens=record.get("input_tokens", 0),
            output_tokens=record.get("output_tokens", 0),
        )
        model["cost_usd"] += record.get("cost_usd", 0.0)
        self.routes[record.get("route", "?")] += 1
        client = self.clients.setdefault(record.get("client", "?"), Counter())
        client["answers"] += 1
        client["cost_usd"] += record.get("cost_usd", 0.0)
        self.tools.update(record.get("tools", []))
        self.answers.update(
            answers=1,
            model_calls=record.get("steps", 0),
            cached_calls=record.get("cached_calls", 0),
            model_retries=record.get("model_retries", 0),
            stopped_at_step_cap=int(record.get("stopped") == "max_steps"),
            cost_usd=record.get("cost_usd", 0.0),
        )

    def add_audit(self, record: dict) -> None:
        """Suma un evento ``audit`` de seguridad."""
        action = record.get("action")
        if action not in SECURITY_ACTIONS:
            return
        self.events[action] += 1
        client = record.get("client")
        if client and action in CLIENT_ACTIONS:
            self.clients.setdefault(client, Counter())[action] += 1

    def snapshot(self, requests: int) -> dict:
        """Los desgloses en JSON (las claves de los códigos van como texto)."""
        answers = self.answers
        rejected = sum(n for code, n in self.statuses.items() if 400 <= code < 500)
        return {
            "by_status": {str(code): n for code, n in sorted(self.statuses.items())},
            "by_model": {
                name: {
                    "answers": counts["answers"],
                    "input_tokens": counts["input_tokens"],
                    "output_tokens": counts["output_tokens"],
                    "cost_usd": round(counts["cost_usd"], COST_DECIMALS),
                }
                for name, counts in self.models.items()
            },
            "by_route": dict(self.routes),
            "by_client": {
                name: {
                    "answers": counts["answers"],
                    "cost_usd": round(counts["cost_usd"], COST_DECIMALS),
                    "rate_limited": counts["rate_limited"],
                    "budget_exceeded": counts["budget_exceeded"],
                }
                for name, counts in self.clients.items()
            },
            "cache_hit_rate": _ratio(answers["cached_calls"], answers["model_calls"]),
            "cost_per_answer_usd": _ratio(
                answers["cost_usd"], answers["answers"], COST_DECIMALS
            ),
            "errors_by_type": {
                name[len("error:"):]: n
                for name, n in self.events.items()
                if name.startswith("error:")
            },
            "security": {
                name: n for name, n in self.events.items() if name in SECURITY_ACTIONS
            },
            "business": {
                "answers": answers["answers"],
                "tools": dict(self.tools),
                "stopped_at_step_cap": answers["stopped_at_step_cap"],
                "fallbacks": self.events["model_fallback"],
                "model_retries": answers["model_retries"],
                "rejected_rate": _ratio(rejected, requests),
            },
        }


class Metrics:
    """Contadores del tráfico de usuarios de este proceso; seguros con varios hilos.

    :param slo_p95_ms: Meta del p95 de latencia en milisegundos
    :type slo_p95_ms: int
    :param window: Cuántas latencias recientes se guardan para los percentiles
    :type window: int
    :param excluded_paths: Rutas operativas que no cuentan
    :type excluded_paths: Iterable[str]
    """

    def __init__(
        self,
        slo_p95_ms: int,
        window: int = LATENCY_WINDOW,
        excluded_paths: Iterable[str] = EXCLUDED_PATHS,
    ):
        self._slo_p95_ms = slo_p95_ms
        self._excluded = frozenset(excluded_paths)
        self._latencies: deque[float] = deque(maxlen=window)
        self._totals = {
            "requests": 0,
            "errors": 0,
            "input": 0,
            "output": 0,
            "cost_usd": 0.0,
        }
        self._breakdown = Breakdown()
        self._lock = threading.Lock()

    def record_request(self, path: str, status: int, latency_ms: float) -> None:
        """Registra una petición terminada; ignora las rutas excluidas.

        :param path: Ruta de la petición
        :type path: str
        :param status: Código HTTP de la respuesta (``5xx`` cuenta como error)
        :type status: int
        :param latency_ms: Latencia en milisegundos
        :type latency_ms: float
        """
        if path in self._excluded:
            return
        with self._lock:
            self._totals["requests"] += 1
            self._totals["errors"] += status >= 500
            self._latencies.append(latency_ms)
            self._breakdown.statuses[status] += 1

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Suma los tokens de una corrida del agente.

        :param input_tokens: Tokens de entrada
        :type input_tokens: int
        :param output_tokens: Tokens de salida
        :type output_tokens: int
        """
        with self._lock:
            self._totals["input"] += input_tokens
            self._totals["output"] += output_tokens

    def add_cost(self, cost_usd: float) -> None:
        """Suma el costo de una respuesta.

        :param cost_usd: Costo en USD
        :type cost_usd: float
        """
        with self._lock:
            self._totals["cost_usd"] += cost_usd

    def observe(self, record: dict) -> None:
        """Cuenta un registro del log (lo llama :class:`app.logs.EventLogger`).

        :param record: Registro ya escrito: ``agent_run``, ``audit``, ``error`` y
            ``model_fallback`` suman a los desgloses; el resto se ignora
        :type record: dict
        """
        event = record.get("event")
        with self._lock:
            if event == "agent_run":
                self._breakdown.add_answer(record)
            elif event == "audit":
                self._breakdown.add_audit(record)
            elif event == "error":
                self._breakdown.events[f"error:{record.get('error_type', '?')}"] += 1
            elif event == "model_fallback":
                self._breakdown.events["model_fallback"] += 1

    def snapshot(self) -> dict:
        """Arma la respuesta de ``GET /metrics``.

        :return: ``pid``, ``requests``, ``error_rate``, ``latency_ms``, ``tokens``,
            ``slo`` y ``cost_usd`` (el contrato), más ``latency_p99_ms`` y los
            desgloses
        :rtype: dict
        """
        with self._lock:
            latencies = list(self._latencies)
            totals = dict(self._totals)
            breakdown = self._breakdown.snapshot(totals["requests"])
        requests = totals["requests"]
        p95 = nearest_rank(latencies, 95)
        return {
            "pid": os.getpid(),
            "requests": requests,
            "error_rate": _ratio(totals["errors"], requests),
            "latency_ms": {"p50": nearest_rank(latencies, 50), "p95": p95},
            "tokens": {"input": totals["input"], "output": totals["output"]},
            "slo": {"p95_ms_target": self._slo_p95_ms, "met": p95 < self._slo_p95_ms},
            "cost_usd": round(totals["cost_usd"], COST_DECIMALS),
            "latency_p99_ms": nearest_rank(latencies, 99),
            **breakdown,
        }
