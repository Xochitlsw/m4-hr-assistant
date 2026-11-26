"""Acceso por cliente: claves y límite de peticiones (ADR 0010).

Las claves se comparan con :func:`hmac.compare_digest` contra todas las claves, sin
cortar en la primera diferencia. Nada de este módulo escribe una clave.
"""

import hmac
import math
import threading
from collections import deque
from collections.abc import Iterable

from app.clock import Clock

WINDOW_SECONDS = 60.0


class KeyStore:
    """Encuentra el cliente de una clave.

    :param api_keys: Pares ``(cliente, clave)``
    :type api_keys: Iterable[tuple[str, str]]
    """

    def __init__(self, api_keys: Iterable[tuple[str, str]]):
        self._keys = tuple(
            (client, key.encode("utf-8")) for client, key in api_keys
        )

    def client_for(self, key: str | None) -> str | None:
        """Devuelve el cliente dueño de la clave, o ``None`` si no hay ninguno.

        :param key: Clave presentada en ``X-API-Key`` (puede faltar)
        :type key: str | None
        :return: Nombre del cliente
        :rtype: str | None
        """
        if not key:
            return None
        presented = key.encode("utf-8")
        found = None
        for client, known in self._keys:
            if hmac.compare_digest(presented, known):
                found = client
        return found


class RateLimiter:
    """Límite de peticiones por cliente con ventana deslizante; seguro entre hilos.

    :param limit: Peticiones permitidas por cliente dentro de la ventana
    :type limit: int
    :param clock: Reloj inyectado (se usa ``monotonic``)
    :type clock: app.clock.Clock
    :param window_seconds: Largo de la ventana
    :type window_seconds: float
    """

    def __init__(
        self, limit: int, clock: Clock, window_seconds: float = WINDOW_SECONDS
    ):
        self._limit = limit
        self._clock = clock
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, client: str) -> int | None:
        """Registra una petición si cabe en el límite.

        :param client: Nombre del cliente
        :type client: str
        :return: ``None`` si se permite; si no, los segundos enteros que faltan para
            que salga de la ventana la petición más antigua (al menos 1)
        :rtype: int | None
        """
        now = self._clock.monotonic()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return max(1, math.ceil(self._window - (now - hits[0])))
            hits.append(now)
            return None
