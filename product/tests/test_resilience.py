"""Resiliencia del modelo real: reintentos, Retry-After, plazo y circuito (ADR 0018).

El gateway es un ``httpx.MockTransport`` que responde una secuencia fija; el reloj, el
``sleep`` y el azar del jitter son de prueba: nada espera de verdad ni sale a la red.
"""

import json

import httpx
import pytest

from app.config import ConfigError, load_settings
from app.gateway import GatewayModel, ModelUnavailable, build_http_client
from app.resilience import ModelCircuitOpen, ResilientModel
from tests.conftest import GATEWAY_ENV

MODEL = "@cf/meta/llama-3.1-8b-instruct-fast"
MESSAGES = [{"role": "user", "content": "¿Quién es EMP-0004?"}]
ANSWER = {
    "choices": [{"message": {"role": "assistant", "content": '{"answer": "Listo"}'}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
}


class ScriptedGateway:
    """Responde, en orden, códigos HTTP o excepciones; el último se repite."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def handle(self, _request):
        self.calls += 1
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        status, headers = step if isinstance(step, tuple) else (step, {})
        if status == 200:
            return httpx.Response(200, json=ANSWER)
        return httpx.Response(status, json={"errors": ["x"]}, headers=headers)


class StepClock:
    """Reloj de prueba: ``monotonic`` avanza ``step`` segundos en cada lectura."""

    def __init__(self, step=0.0):
        self.seconds = 0.0
        self.step = step

    def now(self):
        raise AssertionError("la resiliencia solo usa monotonic")

    def monotonic(self):
        self.seconds += self.step
        return self.seconds


class MidJitter:
    """Azar de prueba: siempre el punto medio, así el jitter multiplica por 1."""

    @staticmethod
    def random():
        return 0.5


def resilient(gateway, clock=None, events=None, **env):
    settings = load_settings({**GATEWAY_ENV, **env}).models
    client = httpx.Client(transport=httpx.MockTransport(gateway.handle))
    sleeps = []
    model = ResilientModel(
        GatewayModel(MODEL, settings, client),
        settings.gateway.resilience,
        clock or StepClock(),
        sleep=sleeps.append,
        rng=MidJitter(),
        on_event=(lambda name, **fields: events.append((name, fields)))
        if events is not None
        else None,
    )
    return model, sleeps


def test_transient_failures_are_retried_with_growing_backoff():
    gateway = ScriptedGateway(503, 502, 200)
    model, sleeps = resilient(gateway)
    completion = model.complete(MESSAGES)
    assert json.loads(completion.text) == {"answer": "Listo"}
    assert (gateway.calls, completion.attempts) == (3, 3)
    assert sleeps == [0.5, 1.0]


def test_network_errors_are_retried_too():
    gateway = ScriptedGateway(httpx.ConnectError("sin red"), 200)
    model, sleeps = resilient(gateway)
    assert model.complete(MESSAGES).attempts == 2
    assert sleeps == [0.5]


def test_a_429_waits_what_retry_after_asks():
    gateway = ScriptedGateway((429, {"Retry-After": "3"}), 200)
    model, sleeps = resilient(gateway)
    model.complete(MESSAGES)
    assert sleeps == [3.0]


def test_a_retry_after_too_long_is_not_waited():
    gateway = ScriptedGateway((429, {"Retry-After": "120"}), 200)
    model, sleeps = resilient(gateway)
    with pytest.raises(ModelUnavailable):
        model.complete(MESSAGES)
    assert (gateway.calls, sleeps) == (1, [])


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_never_retried(status):
    gateway = ScriptedGateway(status)
    model, sleeps = resilient(gateway)
    with pytest.raises(ModelUnavailable):
        model.complete(MESSAGES)
    assert (gateway.calls, sleeps) == (1, [])


def test_no_retry_once_the_call_timeout_is_spent():
    """Un timeout ya gastó el plazo: reintentar solo alargaría la espera."""
    gateway = ScriptedGateway(httpx.ReadTimeout("lento"), 200)
    model, sleeps = resilient(gateway, clock=StepClock(step=31.0))
    with pytest.raises(ModelUnavailable):
        model.complete(MESSAGES)
    assert (gateway.calls, sleeps) == (1, [])


def test_retries_stop_after_the_maximum():
    gateway = ScriptedGateway(500)
    model, sleeps = resilient(gateway, MODEL_MAX_RETRIES="2")
    with pytest.raises(ModelUnavailable):
        model.complete(MESSAGES)
    assert (gateway.calls, len(sleeps)) == (3, 2)


def test_the_circuit_opens_fails_fast_and_recovers():
    clock = StepClock()
    events = []
    gateway = ScriptedGateway(500)
    model, _ = resilient(
        gateway,
        clock=clock,
        events=events,
        MODEL_MAX_RETRIES="0",
        CIRCUIT_FAILURE_THRESHOLD="2",
        CIRCUIT_OPEN_SECONDS="30",
    )
    for _ in range(2):
        with pytest.raises(ModelUnavailable):
            model.complete(MESSAGES)
    assert model.circuit_state == "open"
    with pytest.raises(ModelCircuitOpen) as fast:
        model.complete(MESSAGES)
    assert (gateway.calls, fast.value.retry_after) == (2, 30)
    clock.seconds += 30.0
    gateway.script = [200]
    assert model.complete(MESSAGES).attempts == 1
    assert model.circuit_state == "closed"
    assert [(name, fields["state"]) for name, fields in events] == [
        ("model_circuit", "open"),
        ("model_circuit", "closed"),
    ]


def test_a_failed_trial_reopens_the_circuit():
    clock = StepClock()
    gateway = ScriptedGateway(500)
    model, _ = resilient(
        gateway, clock=clock, MODEL_MAX_RETRIES="0", CIRCUIT_FAILURE_THRESHOLD="1"
    )
    with pytest.raises(ModelUnavailable):
        model.complete(MESSAGES)
    clock.seconds += 30.0
    with pytest.raises(ModelUnavailable):
        model.complete(MESSAGES)
    assert model.circuit_state == "open"
    assert gateway.calls == 2


def test_the_http_client_has_a_short_connect_timeout():
    settings = load_settings({**GATEWAY_ENV, "MODEL_TIMEOUT_SECONDS": "20"}).models
    timeout = build_http_client(settings).timeout
    assert (timeout.connect, timeout.read) == (5.0, 20.0)


def test_resilience_settings_are_validated():
    resilience = load_settings({}).models.gateway.resilience
    assert (
        resilience.max_retries,
        resilience.backoff_seconds,
        resilience.failure_threshold,
        resilience.open_seconds,
        resilience.connect_timeout_seconds,
    ) == (2, 0.5, 5, 30.0, 5.0)
    for name, value in (
        ("MODEL_MAX_RETRIES", "-1"),
        ("MODEL_RETRY_BACKOFF_SECONDS", "-0.5"),
        ("CIRCUIT_FAILURE_THRESHOLD", "0"),
        ("CIRCUIT_OPEN_SECONDS", "0"),
    ):
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})
