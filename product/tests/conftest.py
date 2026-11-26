"""Fixtures y pasos compartidos: la aplicación armada con ``create_app()``.

Las pruebas no dependen de las variables de entorno del proceso: cada una arma su
configuración con ``load_settings()`` a partir de un diccionario, con un reloj fijo y
el log en memoria. Nunca salen a la red: el AI Gateway es falso (``FakeGateway``).
"""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, then

from app.config import load_settings
from app.directory import EmployeeDirectory
from app.llm import FakeModel
from app.logs import EventLogger
from app.main import Observability, create_app
from app.metrics import Metrics
from app.tracing import Tracer

PRODUCT = Path(__file__).resolve().parents[1]
EMPLOYEES = PRODUCT / "data" / "employees.json"
SMALL = "@cf/meta/llama-3.1-8b-instruct-fast"
LARGE = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
GATEWAY_ENV = {
    "MODEL_PROVIDER": "gateway",
    "CF_ACCOUNT_ID": "test-account",
    "CF_API_TOKEN": "test-only-token",
}
# Clave de prueba: no es un secreto, solo existe en estas pruebas.
TEST_CLIENT = "tests"
TEST_KEY = "test-key-0123456789"
ACCESS_ENV = {"API_KEYS": f"{TEST_CLIENT}:{TEST_KEY}"}


class FakeGateway:
    """AI Gateway falso: contesta como Workers AI con las reglas del modelo simulado.

    :param cache_status: Valor de ``cf-aig-cache-status`` en cada respuesta
    :param status: Código HTTP de cada respuesta (distinto de 200 simula una caída)
    """

    def __init__(self, cache_status="MISS", status=200):
        self.requests = []
        self.cache_status = cache_status
        self.status = status
        departments = EmployeeDirectory.load(EMPLOYEES).departments()
        self.model = FakeModel(departments)

    def handle(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"errors": ["unavailable"]})
        completion = self.model.complete(json.loads(request.content)["messages"])
        payload = {
            "choices": [{"message": {"role": "assistant", "content": completion.text}}],
            "usage": {
                "prompt_tokens": completion.input_tokens,
                "completion_tokens": completion.output_tokens,
            },
        }
        headers = {"cf-aig-cache-status": self.cache_status}
        return httpx.Response(200, json=payload, headers=headers)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))


class FixedClock:
    """Reloj de prueba: hora fija y un tiempo monótono que avanza 5 ms por lectura."""

    def __init__(self):
        self.seconds = 0.0

    def now(self):
        return datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

    def monotonic(self):
        self.seconds += 0.005
        return self.seconds


@pytest.fixture
def log_stream():
    """Destino del log en memoria."""
    return io.StringIO()


@pytest.fixture
def log_records(log_stream):
    """Lee las líneas del log ya escritas, como diccionarios."""

    def read():
        return [json.loads(line) for line in log_stream.getvalue().splitlines()]

    return read


@pytest.fixture
def make_client(log_stream):
    """Fábrica de clientes HTTP con la configuración y el modelo que pida la prueba."""

    def build(model=None, tracer=None, http_client=None, **env: str) -> TestClient:
        settings = load_settings({**ACCESS_ENV, **env})
        clock = FixedClock()
        observability = Observability(
            clock=clock,
            logger=EventLogger(settings.observability.service_name, clock, log_stream),
            metrics=Metrics(settings.observability.slo_p95_ms),
            tracer=tracer or Tracer(),
        )
        app = create_app(
            settings=settings,
            model=model,
            observability=observability,
            http_client=http_client,
        )
        return TestClient(app, headers={"X-API-Key": TEST_KEY})

    return build


@pytest.fixture
def client(make_client):
    """Cliente HTTP con la configuración por omisión y el modelo simulado."""
    return make_client()


@pytest.fixture
def directory():
    """El directorio de empleados con los datos del producto."""
    return EmployeeDirectory.load(EMPLOYEES)


@given("the HR Assistant with the fake model", target_fixture="api")
def default_api(make_client):
    return make_client()


@then(parsers.parse("the response status is {status:d}"))
def response_status(response, status):
    assert response.status_code == status


@then(parsers.parse('the answer mentions "{text}"'))
def answer_mentions(response, text):
    assert text in response.json()["answer"]
