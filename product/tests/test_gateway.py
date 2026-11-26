"""Pruebas del modelo real (Workers AI vía AI Gateway) con un cliente HTTP falso."""

import json

import httpx
import pytest

from app.config import load_settings
from app.gateway import GatewayModel, ModelUnavailable, to_protocol
from tests.conftest import GATEWAY_ENV, SMALL, FakeGateway

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
    assert "cf-aig-cache-ttl" not in request.headers
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
    model = gateway_model(gateway.client(), CACHE_TTL_SECONDS="3600")
    assert model.complete(MESSAGES).cached is True
    assert gateway.requests[0].headers["cf-aig-cache-ttl"] == "3600"


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
