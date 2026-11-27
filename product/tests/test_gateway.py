"""Pruebas del modelo real (Workers AI vía AI Gateway) con un cliente HTTP falso."""

import json

import httpx
import pytest

from app.config import load_settings
from app.gateway import CURRENT_REQUEST_ID, GatewayModel, ModelUnavailable, to_protocol
from tests.conftest import GATEWAY_ENV, LARGE, SMALL, FakeGateway

MESSAGES = [{"role": "user", "content": "¿Quién es EMP-0004?"}]
URL = (
    "https://gateway.ai.cloudflare.com/v1/test-account/default"
    "/workers-ai/v1/chat/completions"
)


def gateway_model(client, **env):
    settings = load_settings({**GATEWAY_ENV, **env})
    return GatewayModel(SMALL, settings.models, client)


def test_the_request_follows_the_gateway_contract():
    gateway = FakeGateway()
    completion = gateway_model(gateway.client()).complete(MESSAGES)
    request = gateway.requests[0]
    assert (request.method, str(request.url)) == ("POST", URL)
    assert request.headers["Authorization"] == "Bearer test-only-token"
    # El gateway exige su propia autenticación (Authenticated Gateway).
    assert request.headers["cf-aig-authorization"] == "Bearer test-only-token"
    # Sin TTL, la caché se salta aunque el gateway la tenga encendida en su panel.
    assert request.headers["cf-aig-skip-cache"] == "true"
    assert "cf-aig-cache-ttl" not in request.headers
    assert "cf-aig-cache-key" not in request.headers
    body = json.loads(request.content)
    assert body == {
        "model": SMALL,
        "messages": MESSAGES,
        "max_tokens": 300,
        "temperature": 0.0,
    }
    assert json.loads(completion.text)["tool"] == "get_employee"
    assert completion.input_tokens == 3
    assert completion.output_tokens > 0
    assert completion.cached is False


def test_the_cache_ttl_is_sent_and_a_hit_marks_the_call_as_cached():
    gateway = FakeGateway(cache_status="HIT")
    model = gateway_model(gateway.client(), CACHE_TTL_SECONDS="300")
    assert model.complete(MESSAGES).cached is True
    headers = gateway.requests[0].headers
    assert headers["cf-aig-cache-ttl"] == "300"
    assert len(headers["cf-aig-cache-key"]) == 64
    assert "cf-aig-skip-cache" not in headers


def cache_key(model_name, messages, request_id):
    gateway = FakeGateway()
    settings = load_settings({**GATEWAY_ENV, "CACHE_TTL_SECONDS": "300"})
    model = GatewayModel(model_name, settings.models, gateway.client())
    token = CURRENT_REQUEST_ID.set(request_id)
    try:
        model.complete(messages)
    finally:
        CURRENT_REQUEST_ID.reset(token)
    return gateway.requests[0].headers["cf-aig-cache-key"]


def test_the_cache_key_depends_on_the_request_body_and_not_on_the_request_id():
    """Dos peticiones iguales comparten la caché aunque cada una tenga su id."""
    first = cache_key(SMALL, MESSAGES, "request-1")
    assert cache_key(SMALL, MESSAGES, "request-2") == first
    other_question = [{"role": "user", "content": "¿Quién es EMP-0005?"}]
    assert cache_key(SMALL, other_question, "request-1") != first
    assert cache_key(LARGE, MESSAGES, "request-1") != first


def test_any_gateway_failure_is_model_unavailable():
    def timeout(request):
        raise httpx.ConnectTimeout("too slow", request=request)

    def without_choices(_request):
        return httpx.Response(200, json={"result": "unexpected"})

    clients = [
        FakeGateway(status=500).client(),
        httpx.Client(transport=httpx.MockTransport(timeout)),
        httpx.Client(transport=httpx.MockTransport(without_choices)),
    ]
    for client in clients:
        with pytest.raises(ModelUnavailable) as failure:
            gateway_model(client).complete(MESSAGES)
        assert "test-only-token" not in str(failure.value)


def test_real_model_output_is_normalized_to_the_protocol():
    fenced = '```json\n{"answer": "Hola"}\n```'
    assert json.loads(to_protocol(fenced)) == {"answer": "Hola"}
    chatty = 'Claro: {"tool": "get_employee", "args": {"employee_id": "EMP-0004"}}'
    assert json.loads(to_protocol(chatty))["tool"] == "get_employee"
    prose = "Jorge Díaz es líder técnico {según el directorio}."
    assert json.loads(to_protocol(prose)) == {"answer": prose}
