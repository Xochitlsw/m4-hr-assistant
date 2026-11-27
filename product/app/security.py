"""Acceso por cliente: claves, límites, origen y presupuesto (ADR 0010 y 0017).

Las claves se comparan con :func:`hmac.compare_digest` contra todas las claves, sin
cortar en la primera diferencia. Nada de este módulo escribe una clave.

Las fallas de autenticación se cuentan por **origen** (:func:`client_origin`), y el
origen nunca se registra tal cual: una IP es un dato personal. El log lleva una huella
HMAC con una clave aleatoria por proceso (:class:`OriginFingerprint`).
"""

import hashlib
import hmac
import math
import secrets
import threading
from collections import deque
from collections.abc import Iterable
from datetime import datetime, time, timedelta

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


def client_origin(forwarded_for: str | None, peer: str | None, hops: int) -> str:
    """Origen de una petición, para contar sus fallas de autenticación.

    Detrás de ``hops`` proxies de confianza, cada uno agrega a la derecha de
    ``X-Forwarded-For`` la IP de quien le habló. El origen es el valor que agregó el
    proxy más lejano de confianza: el ``hops``-ésimo desde la derecha. Los valores de
    la izquierda los puede inventar el cliente y no cuentan. Con ``hops=0`` (local), o
    si el encabezado trae menos valores de los esperados, es la IP del socket.

    :param forwarded_for: Valor de ``X-Forwarded-For``, o ``None``
    :type forwarded_for: str | None
    :param peer: IP del socket, o ``None`` si no se conoce
    :type peer: str | None
    :param hops: Proxies de confianza delante de la aplicación
    :type hops: int
    :return: El origen; ``unknown`` si no hay ninguno
    :rtype: str
    """
    if hops > 0 and forwarded_for:
        values = [value.strip() for value in forwarded_for.split(",") if value.strip()]
        if len(values) >= hops:
            return values[-hops]
    return peer or "unknown"


class OriginFingerprint:
    """Huella de un origen para el log: correlaciona eventos sin guardar la IP.

    :param key: Clave del HMAC; si es ``None``, una aleatoria (otra en cada proceso)
    :type key: bytes | None
    """

    def __init__(self, key: bytes | None = None):
        self._key = key if key is not None else secrets.token_bytes(32)

    def __call__(self, origin: str) -> str:
        """Huella de 16 caracteres hexadecimales.

        :param origin: Origen (por ejemplo, una IP)
        :type origin: str
        :return: HMAC-SHA256 truncado
        :rtype: str
        """
        digest = hmac.new(self._key, origin.encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()[:16]


class FailureLimiter:
    """Fallas de autenticación por origen, con ventana deslizante; seguro entre hilos.

    :param limit: Fallas permitidas por origen dentro de la ventana
    :type limit: int
    :param clock: Reloj inyectado (se usa ``monotonic``)
    :type clock: app.clock.Clock
    :param window_seconds: Largo de la ventana
    :type window_seconds: float
    :param max_origins: Orígenes que se recuerdan a la vez (acota la memoria)
    :type max_origins: int
    """

    def __init__(
        self,
        limit: int,
        clock: Clock,
        window_seconds: float = WINDOW_SECONDS,
        max_origins: int = 10_000,
    ):
        self._limit = limit
        self._clock = clock
        self._window = window_seconds
        self._max_origins = max_origins
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def tracked(self) -> int:
        """Orígenes que se recuerdan ahora.

        :return: Cantidad de orígenes con fallas guardadas
        :rtype: int
        """
        with self._lock:
            return len(self._failures)

    def _recent(self, origin: str, now: float) -> deque[float]:
        """Fallas del origen dentro de la ventana (quita las viejas)."""
        failures = self._failures.get(origin, deque())
        while failures and now - failures[0] >= self._window:
            failures.popleft()
        return failures

    def retry_after(self, origin: str) -> int | None:
        """Indica si el origen está bloqueado, sin registrar nada.

        :param origin: Origen de la petición
        :type origin: str
        :return: ``None`` si puede intentar; si no, los segundos enteros que faltan
            para que salga de la ventana su falla más antigua (al menos 1)
        :rtype: int | None
        """
        now = self._clock.monotonic()
        with self._lock:
            failures = self._recent(origin, now)
            if len(failures) < self._limit:
                return None
            return max(1, math.ceil(self._window - (now - failures[0])))

    def record(self, origin: str) -> None:
        """Registra una falla del origen.

        :param origin: Origen de la petición
        :type origin: str
        """
        now = self._clock.monotonic()
        with self._lock:
            full = len(self._failures) >= self._max_origins
            if origin not in self._failures and full:
                self._forget_quiet(now)
            self._failures.setdefault(origin, deque()).append(now)

    def _forget_quiet(self, now: float) -> None:
        """Olvida los orígenes sin fallas recientes; si no alcanza, el más antiguo."""
        for origin in list(self._failures):
            if not self._recent(origin, now):
                del self._failures[origin]
        if len(self._failures) >= self._max_origins:
            del self._failures[next(iter(self._failures))]


class DailyBudget:
    """Costo por cliente y por día UTC (OWASP LLM10); seguro entre hilos.

    El costo se suma después de cada respuesta, así que una petición que empieza dentro
    del presupuesto puede pasarlo por el costo de esa sola respuesta.

    :param limit_usd: Presupuesto diario por cliente en USD; 0 lo apaga
    :type limit_usd: float
    :param clock: Reloj inyectado (se usa ``now``, en UTC)
    :type clock: app.clock.Clock
    """

    def __init__(self, limit_usd: float, clock: Clock):
        self.limit_usd = limit_usd
        self._clock = clock
        self._spent: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def _today(self, client: str) -> float:
        """Lo gastado hoy por el cliente (0 si su registro es de otro día)."""
        day, spent = self._spent.get(client, (None, 0.0))
        return spent if day == self._clock.now().date() else 0.0

    def retry_after(self, client: str) -> int | None:
        """Indica si el cliente agotó su presupuesto de hoy.

        :param client: Nombre del cliente
        :type client: str
        :return: ``None`` si puede preguntar; si no, los segundos que faltan para la
            medianoche UTC (al menos 1)
        :rtype: int | None
        """
        if self.limit_usd <= 0:
            return None
        with self._lock:
            if self._today(client) < self.limit_usd:
                return None
        now = self._clock.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time(), now.tzinfo)
        return max(1, math.ceil((midnight - now).total_seconds()))

    def add(self, client: str, cost_usd: float) -> None:
        """Suma el costo de una respuesta al día del cliente.

        :param client: Nombre del cliente
        :type client: str
        :param cost_usd: Costo en USD
        :type cost_usd: float
        """
        with self._lock:
            self._spent[client] = (
                self._clock.now().date(),
                self._today(client) + cost_usd,
            )
