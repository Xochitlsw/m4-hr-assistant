"""Pruebas de las trazas en LangSmith, sin red.

``Recorder`` crea clientes reales de LangSmith (con su lógica de ocultar entradas y
salidas) sobre una sesión HTTP falsa que guarda los cuerpos en lugar de enviarlos.
"""

import json

from langsmith import Client

from app.config import load_settings
from app.llm import Completion
from app.privacy import redact_tree
from app.tracing import Tracer
from tests.conftest import GATEWAY_ENV, FakeGateway

TRACING_ON = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "test-only-key"}
REDACTED_TRACES = {**TRACING_ON, "HIDE_INPUTS": "redact", "HIDE_OUTPUTS": "redact"}
CURP = "XAXX010101HDFXXX09"
EMAIL = "persona.prueba@example.com"


class RecordedResponse:
    """Respuesta exitosa vacía."""

    status_code = 200
    headers: dict = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class Recorder:
    """Sesión HTTP falsa para el cliente de LangSmith: guarda lo que enviaría."""

    def __init__(self):
        self.options = []
        self.bodies = []

    def client(self, **options) -> Client:
        self.options.append(options)
        return Client(
            api_key="test-only-key",
            api_url="http://langsmith.invalid",
            auto_batch_tracing=False,
            session=self,
            **options,
        )

    def mount(self, _prefix, _adapter):
        """El cliente monta su adaptador HTTP; aquí no hay red."""

    def close(self):
        """No hay conexiones que cerrar."""

    def request(self, method, url, data=None, **_ignored):
        assert method in {"POST", "PATCH"} and "/runs" in url
        self.bodies.append(json.loads(data or b"{}"))
        return RecordedResponse()


def test_tracing_needs_both_the_flag_and_the_api_key():
    recorder = Recorder()
    only_flag = load_settings({"LANGSMITH_TRACING": "true"})
    only_key = load_settings({"LANGSMITH_API_KEY": "test-only-key"})
    for settings in (only_flag, only_key):
        assert not settings.observability.tracing_enabled
        assert not Tracer.from_settings(settings, recorder.client).enabled
    assert not recorder.options
    assert Tracer.from_settings(load_settings(TRACING_ON), recorder.client).enabled
    assert recorder.options == [{"hide_inputs": True, "hide_outputs": True}]


def test_traced_requests_use_the_hiding_client_and_carry_the_request_id(make_client):
    recorder = Recorder()
    tracer = Tracer.from_settings(load_settings(TRACING_ON), recorder.client)
    api = make_client(tracer=tracer)
    response = api.post(
        "/ask",
        json={"question": "¿Quién es EMP-0004?"},
        headers={"X-Request-ID": "demo-123"},
    )
    assert response.status_code == 200
    names = {body["name"] for body in recorder.bodies if "name" in body}
    assert {"agent_run", "model", "get_employee"} <= names
    sent = json.dumps(recorder.bodies, ensure_ascii=False)
    for text in ("EMP-0004", "Quién", "Jorge Díaz", "Resultado de la herramienta"):
        assert text not in sent
    for body in recorder.bodies:
        assert body.get("inputs") in (None, {})
        assert body.get("outputs") in (None, {})
        assert body["extra"]["metadata"]["request_id"] == "demo-123"
        assert body["extra"]["metadata"]["client"] == "tests"


class LeakyModel:
    """Modelo que pide la herramienta y después responde con un dato personal."""

    def __init__(self):
        self.replies = [
            json.dumps({"tool": "get_employee", "args": {"employee_id": "EMP-0004"}}),
            json.dumps({"answer": f"Jorge Díaz; su correo es {EMAIL}"}),
        ]

    def complete(self, _messages):
        return Completion(self.replies.pop(0), 1, 1)


def test_redacted_traces_use_the_detector_as_anonymizer():
    recorder = Recorder()
    mixed = {**TRACING_ON, "HIDE_INPUTS": "true", "HIDE_OUTPUTS": "redact"}
    Tracer.from_settings(load_settings(REDACTED_TRACES), recorder.client)
    Tracer.from_settings(load_settings(mixed), recorder.client)
    assert recorder.options == [
        {"hide_inputs": False, "hide_outputs": False, "anonymizer": redact_tree},
        {"hide_inputs": True, "hide_outputs": False, "anonymizer": redact_tree},
    ]


def test_redacted_traces_keep_the_directory_and_drop_personal_data(make_client):
    recorder = Recorder()
    tracer = Tracer.from_settings(load_settings(REDACTED_TRACES), recorder.client)
    api = make_client(tracer=tracer, model=LeakyModel())
    response = api.post(
        "/ask", json={"question": f"Mi CURP es {CURP}, ¿quién es EMP-0004?"}
    )
    assert response.status_code == 200
    sent = json.dumps(recorder.bodies, ensure_ascii=False)
    assert CURP not in sent and EMAIL not in sent
    for useful in ("EMP-0004", "Jorge Díaz", "[REDACTED:CURP]", "[REDACTED:EMAIL]"):
        assert useful in sent


def runs_by_name(recorder):
    """Las corridas enviadas, por nombre, con lo que tenga el cuerpo final."""
    runs = {}
    for body in recorder.bodies:
        run = runs.setdefault(body.get("id"), {})
        run.update({key: value for key, value in body.items() if value is not None})
    named = {}
    for run in runs.values():
        named.setdefault(run.get("name"), []).append(run)
    return named


def test_each_question_is_one_tree_with_useful_metadata(make_client):
    recorder = Recorder()
    tracer = Tracer.from_settings(load_settings(REDACTED_TRACES), recorder.client)
    api = make_client(tracer=tracer)
    api.post(
        "/ask",
        json={"question": "¿Quién es EMP-0004?"},
        headers={"X-Request-ID": "tree-1"},
    )
    named = runs_by_name(recorder)
    [ask] = named["ask"]
    [agent_run] = named["agent_run"]
    models = named["model"]
    [tool] = named["get_employee"]
    assert agent_run["parent_run_id"] == ask["id"]
    assert {run["parent_run_id"] for run in models} == {agent_run["id"]}
    assert tool["parent_run_id"] == agent_run["id"]
    metadata = ask["extra"]["metadata"]
    for key in ("request_id", "client", "environment", "model", "route", "cost_usd",
                "steps", "stopped", "cached_calls", "model_retries", "fallback"):
        assert key in metadata, key
    assert (metadata["route"], metadata["fallback"]) == ("simple", False)
    assert "hr-assistant" in ask.get("tags", [])
    llm = models[0]["extra"]["metadata"]
    assert llm["ls_model_name"] == "@cf/meta/llama-3.1-8b-instruct-fast"
    assert llm["ls_provider"] == "fake"
    assert llm["usage_metadata"]["total_tokens"] > 0


def test_the_gateway_token_never_reaches_the_traces_or_the_log(make_client, log_stream):
    """El token viaja en dos cabeceras al gateway, pero nunca a LangSmith ni al log."""
    recorder = Recorder()
    tracer = Tracer.from_settings(load_settings(REDACTED_TRACES), recorder.client)
    gateway = FakeGateway()
    api = make_client(tracer=tracer, http_client=gateway.client(), **GATEWAY_ENV)
    assert api.post("/ask", json={"question": "¿Quién es EMP-0004?"}).status_code == 200
    assert all(
        request.headers["cf-aig-authorization"] == "Bearer test-only-token"
        for request in gateway.requests
    )
    assert "test-only-token" not in json.dumps(recorder.bodies)
    assert "test-only-token" not in log_stream.getvalue()
