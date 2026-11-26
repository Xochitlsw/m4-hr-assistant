"""Pruebas de las trazas en LangSmith, sin red.

``Recorder`` crea clientes reales de LangSmith (con su lógica de ocultar entradas y
salidas) sobre una sesión HTTP falsa que guarda los cuerpos en lugar de enviarlos.
"""

import json

from langsmith import Client

from app.config import load_settings
from app.tracing import Tracer

TRACING_ON = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "test-only-key"}


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
