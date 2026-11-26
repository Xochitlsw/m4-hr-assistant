"""Pruebas de la configuración: valores por omisión y validación al arrancar."""

from pathlib import Path

import pytest

from app.config import ConfigError, load_settings
from app.main import create_app

PRODUCT = Path(__file__).resolve().parents[1]


def test_defaults_come_from_the_spec():
    settings = load_settings({})
    assert settings.models.provider == "fake"
    assert settings.agent_max_steps == 3
    assert settings.max_question_chars == 2000
    assert settings.employees_path == PRODUCT / "data" / "employees.json"


def test_invalid_values_stop_the_start_with_a_clear_message():
    invalid = [
        ("AGENT_MAX_STEPS", "0"),
        ("AGENT_MAX_STEPS", "many"),
        ("MAX_QUESTION_CHARS", "-5"),
        ("MODEL_PROVIDER", "gpt"),
    ]
    for name, value in invalid:
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})


def test_observability_defaults_are_safe():
    observability = load_settings({}).observability
    assert observability.service_name == "hr-assistant"
    assert observability.slo_p95_ms == 2000
    assert observability.tracing_enabled is False
    assert (observability.hide_inputs, observability.hide_outputs) == (True, True)


def test_invalid_observability_values_stop_the_start():
    invalid = [
        ("SLO_P95_MS", "0"),
        ("LANGSMITH_TRACING", "yes"),
        ("HIDE_INPUTS", "maybe"),
        ("HIDE_OUTPUTS", ""),
    ]
    for name, value in invalid:
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})


def test_cost_defaults_come_from_the_spec():
    settings = load_settings({})
    models, routing = settings.models, settings.routing
    assert models.small == "@cf/meta/llama-3.1-8b-instruct-fast"
    assert models.large == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert (models.max_tokens, models.temperature) == (300, 0.0)
    assert models.prices_path == PRODUCT / "data" / "model_prices.json"
    gateway = models.gateway
    assert (gateway.gateway_id, gateway.cache_ttl_seconds) == ("default", 0)
    assert gateway.timeout_seconds == 30.0
    assert (routing.enabled, routing.max_chars, routing.history_window) == (
        True,
        80,
        6,
    )


def test_the_gateway_needs_its_three_cloudflare_variables():
    complete = {
        "MODEL_PROVIDER": "gateway",
        "CF_ACCOUNT_ID": "test-account",
        "CF_GATEWAY_ID": "default",
        "CF_API_TOKEN": "test-only-token",
    }
    settings = load_settings(complete)
    assert "test-only-token" not in repr(settings)
    for name in ("CF_ACCOUNT_ID", "CF_GATEWAY_ID", "CF_API_TOKEN"):
        with pytest.raises(ConfigError, match=name) as failure:
            load_settings({**complete, name: " "})
        assert "test-only-token" not in str(failure.value)


def test_invalid_cost_values_stop_the_start():
    invalid = [
        ("ROUTING", "maybe"),
        ("ROUTER_MAX_CHARS", "0"),
        ("HISTORY_WINDOW", "-1"),
        ("MODEL_MAX_TOKENS", "0"),
        ("MODEL_TEMPERATURE", "3"),
        ("MODEL_TIMEOUT_SECONDS", "0"),
        ("CACHE_TTL_SECONDS", "-1"),
    ]
    for name, value in invalid:
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})


def test_security_defaults_and_invalid_values():
    settings = load_settings({})
    security = settings.security
    assert security.environment == "development"
    assert (security.api_keys, security.rate_limit_per_minute) == ((), 30)
    with_key = load_settings({"API_KEYS": "portal:portal-key-0123456789"})
    assert with_key.security.clients == ("portal",)
    assert "portal-key-0123456789" not in repr(with_key)
    for name, value in (("ENVIRONMENT", "staging"), ("RATE_LIMIT_PER_MINUTE", "0")):
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})


def test_a_missing_employees_file_stops_the_start(tmp_path):
    settings = load_settings({"EMPLOYEES_PATH": str(tmp_path / "missing.json")})
    with pytest.raises(ConfigError, match="EMPLOYEES_PATH"):
        create_app(settings=settings)
