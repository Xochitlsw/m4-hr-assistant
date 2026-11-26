"""Disponibilidad por HTTP: fallback, circuito abierto, /ready y reintentos (ADR 0018).

El AI Gateway es falso: responde como Workers AI con las reglas del modelo simulado, o
falla para el modelo que la prueba diga. Sin espera real: el backoff vale 0.
"""

import json

import httpx

from tests.conftest import GATEWAY_ENV, LARGE, SMALL, FakeGateway

COMPLEX = "Compara a los empleados de Tecnología"
FAST = {**GATEWAY_ENV, "MODEL_RETRY_BACKOFF_SECONDS": "0"}


class FlakyGateway(FakeGateway):
    """Gateway falso que falla con 500 para los modelos de ``failing``.

    :param failing: Nombres de los modelos que fallan
    :param fail_first: Cuántas llamadas fallan antes de responder bien (``None``:
        siempre)
    """

    def __init__(self, failing, fail_first=None):
        super().__init__()
        self.failing = set(failing)
        self.fail_first = fail_first
        self.failed = 0

    def handle(self, request):
        model = json.loads(request.content)["model"]
        if model in self.failing and (
            self.fail_first is None or self.failed < self.fail_first
        ):
            self.failed += 1
            self.requests.append(request)
            return httpx.Response(500, json={"errors": ["down"]})
        return super().handle(request)


def events(log_records, name):
    return [record for record in log_records() if record["event"] == name]


def test_when_the_large_model_fails_the_small_one_answers(make_client, log_records):
    gateway = FlakyGateway({LARGE})
    client = make_client(http_client=gateway.client(), **FAST)
    response = client.post("/ask", json={"question": COMPLEX})
    body = response.json()
    assert response.status_code == 200
    assert (body["model"], body["route"]) == (SMALL, "keyword:compara+fallback")
    [fallback] = events(log_records, "model_fallback")
    assert (fallback["from_model"], fallback["to_model"]) == (LARGE, SMALL)


def test_if_both_models_fail_the_answer_is_502(make_client):
    gateway = FlakyGateway({LARGE, SMALL})
    client = make_client(http_client=gateway.client(), **FAST)
    response = client.post("/ask", json={"question": COMPLEX})
    assert response.status_code == 502
    assert response.json()["detail"] == "model unavailable"


def test_with_both_circuits_open_the_answer_is_503_and_ready_says_no(make_client):
    gateway = FlakyGateway({LARGE, SMALL})
    client = make_client(
        http_client=gateway.client(), CIRCUIT_FAILURE_THRESHOLD="1", **FAST
    )
    assert client.get("/ready").status_code == 200
    assert client.post("/ask", json={"question": COMPLEX}).status_code == 502
    calls = len(gateway.requests)
    response = client.post("/ask", json={"question": COMPLEX})
    assert response.status_code == 503
    assert response.json()["detail"] == "model temporarily unavailable"
    assert int(response.headers["Retry-After"]) >= 1
    assert len(gateway.requests) == calls
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert set(ready.json()["checks"]["models"].values()) == {"open"}


def test_ready_reports_its_checks_without_a_key_and_out_of_the_metrics(make_client):
    client = make_client()
    ready = client.get("/ready", headers={"X-API-Key": ""})
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {
            "directory": "ok",
            "prices": "ok",
            "models": {SMALL: "closed", LARGE: "closed"},
        },
    }
    assert client.get("/metrics").json()["requests"] == 0


def test_a_retried_call_is_counted_in_the_agent_run_log(make_client, log_records):
    gateway = FlakyGateway({SMALL}, fail_first=1)
    client = make_client(http_client=gateway.client(), **FAST)
    response = client.post("/ask", json={"question": "¿Quién es EMP-0004?"})
    assert response.status_code == 200
    [run] = events(log_records, "agent_run")
    assert run["model_retries"] == 1
