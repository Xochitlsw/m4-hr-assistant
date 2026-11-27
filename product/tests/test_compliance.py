"""Pruebas de compliance como código: producción no arranca si no cumple."""

import pytest

from app import compliance
from app.config import ConfigError, load_settings
from app.main import create_app
from tests.conftest import GATEWAY_ENV, FakeGateway

SAFE_PRODUCTION = {
    **GATEWAY_ENV,
    "ENVIRONMENT": "production",
    "API_KEYS": "portal:portal-key-0123456789",
}


def test_production_refuses_unsafe_settings():
    unsafe = {
        "MODEL_PROVIDER must not be fake": {"MODEL_PROVIDER": "fake"},
        "API_KEYS must define at least one client": {"API_KEYS": ""},
        "client 'smoke' must have at least 16 characters": {
            "API_KEYS": "portal:portal-key-0123456789,smoke:short-secret"
        },
        "HIDE_INPUTS and HIDE_OUTPUTS must be true": {
            "LANGSMITH_TRACING": "true",
            "HIDE_OUTPUTS": "false",
        },
        "MAX_BODY_BYTES must be at most 1048576": {"MAX_BODY_BYTES": "2000000"},
        "CLIENT_DAILY_BUDGET_USD must be greater than 0": {
            "CLIENT_DAILY_BUDGET_USD": "0"
        },
        "MAX_HISTORY_MESSAGES must be at most 50": {"MAX_HISTORY_MESSAGES": "51"},
        "AGENT_MAX_STEPS must be at most 5": {"AGENT_MAX_STEPS": "6"},
        "MODEL_MAX_TOKENS must be at most 1024": {"MODEL_MAX_TOKENS": "4096"},
        "CACHE_TTL_SECONDS must be at most 900": {"CACHE_TTL_SECONDS": "3600"},
        "must be true (hide) or redact, never false": {
            "LANGSMITH_TRACING": "true",
            "HIDE_INPUTS": "redact",
            "HIDE_OUTPUTS": "false",
        },
    }
    for rule, change in unsafe.items():
        settings = load_settings({**SAFE_PRODUCTION, **change})
        with pytest.raises(ConfigError) as failure:
            compliance.enforce(settings)
        message = str(failure.value)
        assert message.startswith("ENVIRONMENT=production refuses to start: ")
        assert rule in message
        assert "short-secret" not in message


@pytest.mark.parametrize(
    "inputs, outputs", [("true", "true"), ("redact", "redact"), ("true", "redact")]
)
def test_production_traces_may_be_hidden_or_redacted(inputs, outputs):
    traced = {
        **SAFE_PRODUCTION,
        "LANGSMITH_TRACING": "true",
        "HIDE_INPUTS": inputs,
        "HIDE_OUTPUTS": outputs,
    }
    assert not compliance.violations(load_settings(traced))


def test_a_safe_production_and_any_development_setup_start():
    settings = load_settings(SAFE_PRODUCTION)
    assert not compliance.violations(settings)
    assert create_app(settings=settings, http_client=FakeGateway().client())
    development = load_settings({"MODEL_PROVIDER": "fake", "API_KEYS": "a:short"})
    assert compliance.violations(development)
    compliance.enforce(development)
