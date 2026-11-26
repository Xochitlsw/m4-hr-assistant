"""Palancas 1 y 2: enrutamiento por regla y ventana de historial (ADR 0007).

El enrutador es una regla en código, no otro modelo: gratis, determinista y
explicable. ``route`` dice por qué se eligió cada modelo.
"""

from collections.abc import Sequence
from typing import NamedTuple

from app.config import RoutingSettings
from app.directory import normalize

# Palabras que piden razonar sobre varios datos, ya sin mayúsculas ni acentos.
KEYWORDS = ("compara", "resume", "explica", "por que", "analiza", "todos")


class RouteDecision(NamedTuple):
    """Modelo elegido para una pregunta y el motivo.

    :param model: Nombre del modelo
    :type model: str
    :param route: ``routing_off``, ``long_question``, ``keyword:<palabra>`` o ``simple``
    :type route: str
    """

    model: str
    route: str


class Router:
    """Elige el modelo pequeño o el grande para cada pregunta.

    :param settings: Configuración de la palanca (``ROUTING``, ``ROUTER_MAX_CHARS``)
    :type settings: app.config.RoutingSettings
    :param small: Nombre del modelo barato
    :type small: str
    :param large: Nombre del modelo capaz
    :type large: str
    """

    def __init__(self, settings: RoutingSettings, small: str, large: str):
        self._settings = settings
        self._small = small
        self._large = large

    def choose(self, question: str) -> RouteDecision:
        """Aplica la regla: grande si está apagado, es larga o tiene palabra clave.

        :param question: Pregunta ya validada
        :type question: str
        :return: El modelo y el motivo
        :rtype: RouteDecision
        """
        if not self._settings.enabled:
            return RouteDecision(self._large, "routing_off")
        if len(question) > self._settings.max_chars:
            return RouteDecision(self._large, "long_question")
        text = normalize(question)
        for keyword in KEYWORDS:
            if keyword in text:
                return RouteDecision(self._large, f"keyword:{keyword}")
        return RouteDecision(self._small, "simple")


def apply_window(history: Sequence[dict], window: int) -> list[dict]:
    """Deja solo los últimos ``window`` mensajes del historial (palanca 2).

    :param history: Historial completo que mandó el cliente
    :type history: Sequence[dict]
    :param window: Mensajes que se conservan; 0 no deja ninguno
    :type window: int
    :return: Los mensajes más recientes, en orden
    :rtype: list[dict]
    """
    if window <= 0:
        return []
    return list(history[-window:])
