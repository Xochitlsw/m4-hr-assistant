"""Métricas en memoria de este proceso y su SLO de latencia (ADR 0005)."""

import math
import os
import threading
from collections import deque
from collections.abc import Iterable, Sequence

EXCLUDED_PATHS = frozenset({"/health", "/metrics"})
LATENCY_WINDOW = 1000
COST_DECIMALS = 8


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
        self._counts = {"requests": 0, "errors": 0}
        self._tokens = {"input": 0, "output": 0}
        self._cost_usd = 0.0
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
            self._counts["requests"] += 1
            self._counts["errors"] += status >= 500
            self._latencies.append(latency_ms)

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Suma los tokens de una corrida del agente.

        :param input_tokens: Tokens de entrada
        :type input_tokens: int
        :param output_tokens: Tokens de salida
        :type output_tokens: int
        """
        with self._lock:
            self._tokens["input"] += input_tokens
            self._tokens["output"] += output_tokens

    def add_cost(self, cost_usd: float) -> None:
        """Suma el costo de una respuesta.

        :param cost_usd: Costo en USD
        :type cost_usd: float
        """
        with self._lock:
            self._cost_usd += cost_usd

    def snapshot(self) -> dict:
        """Arma la respuesta de ``GET /metrics``.

        :return: ``pid``, ``requests``, ``error_rate``, ``latency_ms``, ``tokens``,
            ``slo`` y ``cost_usd`` acumulado
        :rtype: dict
        """
        with self._lock:
            latencies = list(self._latencies)
            counts = dict(self._counts)
            tokens = dict(self._tokens)
            cost_usd = self._cost_usd
        requests = counts["requests"]
        p95 = nearest_rank(latencies, 95)
        return {
            "pid": os.getpid(),
            "requests": requests,
            "error_rate": round(counts["errors"] / requests, 4) if requests else 0.0,
            "latency_ms": {"p50": nearest_rank(latencies, 50), "p95": p95},
            "tokens": tokens,
            "slo": {"p95_ms_target": self._slo_p95_ms, "met": p95 < self._slo_p95_ms},
            "cost_usd": round(cost_usd, COST_DECIMALS),
        }
