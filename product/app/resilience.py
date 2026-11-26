"""Resiliencia del modelo real: reintentos con backoff y circuit breaker (ADR 0018).

:class:`ResilientModel` envuelve un modelo (``complete(messages)``):

- **Reintentos**: hasta ``max_retries`` más, solo si la falla es transitoria (error de
  red, ``429`` o ``5xx``; nunca otros ``4xx`` ni una respuesta inútil). Espera
  ``backoff_seconds × 2^(n-1)`` con jitter (entre 0.5 y 1.5 veces). Si el gateway pide
  ``Retry-After``, espera eso, salvo que pase de :data:`MAX_RETRY_AFTER_SECONDS`: ahí no
  reintenta. Tampoco reintenta si ya se gastó el tiempo de una llamada
  (``MODEL_TIMEOUT_SECONDS``) desde el primer intento: un timeout no se repite.
- **Circuit breaker**: tras ``failure_threshold`` llamadas fallidas seguidas, el
  circuito se abre ``open_seconds`` y cada llamada falla al instante con
  :class:`ModelCircuitOpen` (la API responde ``503`` con ``Retry-After``). Después deja
  pasar una llamada de prueba: si responde, se cierra; si no, se vuelve a abrir.

El reloj, ``sleep`` y el azar se inyectan: las pruebas no esperan. Es seguro entre
hilos.
"""

import math
import random
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from app.clock import Clock
from app.config import ResilienceSettings
from app.gateway import ModelUnavailable
from app.llm import ChatModel, Completion

MAX_RETRY_AFTER_SECONDS = 10.0
EventCallback = Callable[..., None]


class ModelCircuitOpen(ModelUnavailable):
    """El circuito del modelo está abierto: falla rápido sin llamar al gateway.

    :param model: Nombre del modelo
    :type model: str
    :param retry_after: Segundos enteros que faltan para la llamada de prueba
    :type retry_after: int
    """

    def __init__(self, model: str, retry_after: int):
        super().__init__(f"circuit open for {model}", retryable=False)
        self.retry_after = retry_after


class CircuitBreaker:
    """Estado del circuito de un modelo: ``closed``, ``open`` o ``half_open``.

    :param threshold: Fallas seguidas que abren el circuito
    :type threshold: int
    :param open_seconds: Tiempo abierto antes de la llamada de prueba
    :type open_seconds: float
    :param clock: Reloj inyectado (se usa ``monotonic``)
    :type clock: app.clock.Clock
    """

    def __init__(self, threshold: int, open_seconds: float, clock: Clock):
        self._threshold = threshold
        self._open_seconds = open_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Estado actual del circuito.

        :return: ``closed``, ``open`` o ``half_open``
        :rtype: str
        """
        with self._lock:
            if self._opened_at is None:
                return "closed"
            waited = self._clock.monotonic() - self._opened_at
            return "half_open" if waited >= self._open_seconds else "open"

    def before_call(self) -> int | None:
        """Decide si una llamada puede pasar.

        :return: ``None`` si pasa; si no, los segundos que faltan (al menos 1)
        :rtype: int | None
        """
        with self._lock:
            if self._opened_at is None:
                return None
            waited = self._clock.monotonic() - self._opened_at
            if waited < self._open_seconds:
                return max(1, math.ceil(self._open_seconds - waited))
            if self._trial:
                return 1
            self._trial = True
            return None

    def record(self, ok: bool) -> str | None:
        """Registra el resultado de una llamada.

        :param ok: ``True`` si el modelo respondió
        :type ok: bool
        :return: El estado nuevo si cambió (``open`` o ``closed``); si no, ``None``
        :rtype: str | None
        """
        with self._lock:
            was_open = self._opened_at is not None
            self._trial = False
            if ok:
                self._failures = 0
                self._opened_at = None
                return "closed" if was_open else None
            self._failures += 1
            if was_open or self._failures >= self._threshold:
                self._opened_at = self._clock.monotonic()
                return "open"
            return None


class Retrier:
    """Reintentos con backoff, jitter, ``Retry-After`` y plazo; ver el módulo.

    :param policy: Reintentos y backoff
    :type policy: app.config.ResilienceSettings
    :param clock: Reloj inyectado (se usa ``monotonic``)
    :type clock: app.clock.Clock
    :param timeout_seconds: Tiempo de una llamada; pasado eso desde el primer intento,
        no se reintenta
    :type timeout_seconds: float
    :param sleep: Función que espera (las pruebas guardan los valores)
    :type sleep: Callable[[float], None]
    :param rng: Fuente del jitter (``random()`` entre 0 y 1)
    :type rng: random.Random
    """

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        policy: ResilienceSettings,
        clock: Clock,
        timeout_seconds: float,
        sleep: Callable[[float], None],
        rng: random.Random,
    ):
        self._policy = policy
        self._clock = clock
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._rng = rng

    def call(self, model: ChatModel, messages: list[dict]) -> Completion:
        """Intenta hasta ``1 + max_retries`` veces.

        :param model: Modelo a llamar
        :type model: app.llm.ChatModel
        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta, con ``attempts``
        :rtype: app.llm.Completion
        :raises app.gateway.ModelUnavailable: Si no se reintenta más
        """
        started = self._clock.monotonic()
        attempt = 1
        while True:
            try:
                return replace(model.complete(messages), attempts=attempt)
            except ModelUnavailable as error:
                wait = self._wait_before_retry(error, attempt, started)
                if wait is None:
                    raise
                self._sleep(wait)
                attempt += 1

    def _wait_before_retry(
        self, error: ModelUnavailable, attempt: int, started: float
    ) -> float | None:
        """Segundos antes del siguiente intento, o ``None`` si no se reintenta."""
        if not error.retryable or attempt > self._policy.max_retries:
            return None
        if self._clock.monotonic() - started >= self._timeout:
            return None
        if error.retry_after is not None:
            if error.retry_after > MAX_RETRY_AFTER_SECONDS:
                return None
            return float(error.retry_after)
        jitter = 0.5 + self._rng.random()
        return self._policy.backoff_seconds * 2 ** (attempt - 1) * jitter


class ResilientModel:
    """Un modelo con reintentos, plazo y circuit breaker.

    :param model: Modelo que envuelve (con su precio)
    :type model: app.llm.ChatModel
    :param policy: Reintentos, backoff, umbral y tiempo abierto
    :type policy: app.config.ResilienceSettings
    :param clock: Reloj inyectado
    :type clock: app.clock.Clock
    :param timeout_seconds: Tiempo de una llamada (plazo de los reintentos)
    :type timeout_seconds: float
    :param sleep: Función que espera (las pruebas guardan los valores)
    :type sleep: Callable[[float], None]
    :param rng: Fuente del jitter (``random()`` entre 0 y 1)
    :type rng: random.Random
    :param on_event: Aviso de cambios de estado del circuito (``model_circuit``)
    :type on_event: Callable[..., None] | None
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        model: ChatModel,
        policy: ResilienceSettings,
        clock: Clock,
        *,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        on_event: EventCallback | None = None,
    ):
        self.name = getattr(model, "name", "model")
        self._model = model
        self._retrier = Retrier(
            policy, clock, timeout_seconds, sleep, rng or random.Random()
        )
        self._circuit = CircuitBreaker(
            policy.failure_threshold, policy.open_seconds, clock
        )
        self._on_event = on_event

    @property
    def circuit_state(self) -> str:
        """Estado del circuito del modelo (``/ready`` lo reporta).

        :return: ``closed``, ``open`` o ``half_open``
        :rtype: str
        """
        return self._circuit.state

    def complete(self, messages: list[dict]) -> Completion:
        """Llama al modelo con reintentos, dentro del circuito.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta, con ``attempts`` = intentos que hicieron falta
        :rtype: app.llm.Completion
        :raises ModelCircuitOpen: Si el circuito está abierto
        :raises app.gateway.ModelUnavailable: Si el modelo falla tras los reintentos
        """
        blocked = self._circuit.before_call()
        if blocked is not None:
            raise ModelCircuitOpen(self.name, blocked)
        try:
            completion = self._retrier.call(self._model, messages)
        except ModelUnavailable:
            self._notify(self._circuit.record(ok=False))
            raise
        self._notify(self._circuit.record(ok=True))
        return completion

    def _notify(self, state: str | None) -> None:
        """Avisa un cambio de estado del circuito, si lo hubo."""
        if state is not None and self._on_event is not None:
            self._on_event("model_circuit", model=self.name, state=state)
