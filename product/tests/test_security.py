"""Pruebas de las claves por cliente y del límite de peticiones."""

import pytest

from app.config import ConfigError, parse_api_keys
from app.security import KeyStore, RateLimiter


class ManualClock:
    """Reloj que solo avanza cuando la prueba lo pide."""

    def __init__(self):
        self.seconds = 0.0

    def now(self):
        raise AssertionError("el límite solo usa el reloj monótono")

    def monotonic(self):
        return self.seconds


def test_api_keys_are_parsed_into_clients():
    raw = " portal:portal-key-0123456789 , smoke:smoke-key-0123456789 ,"
    assert parse_api_keys(raw) == (
        ("portal", "portal-key-0123456789"),
        ("smoke", "smoke-key-0123456789"),
    )
    assert not parse_api_keys("")


def test_malformed_api_keys_never_show_the_key():
    cases = {
        "secret-value-without-client": "entry 1 is invalid (length 27)",
        "ok:first-key-0123456,secret-without-client": "entry 2 is invalid (length 21)",
        "portal:": "entry 1 is invalid (length 7)",
        ":secret-without-name": "entry 1 is invalid (length 20)",
        "a:secret-one-0123456,a:secret-two-0123456": "client 'a' is repeated",
        "a:secret-same-0123456,b:secret-same-0123456": "entry 2 repeats a key",
    }
    for raw, message in cases.items():
        with pytest.raises(ConfigError, match="API_KEYS") as failure:
            parse_api_keys(raw)
        assert message in str(failure.value)
        assert "secret" not in str(failure.value)
        assert failure.value.__cause__ is None


def test_the_key_store_finds_the_client_of_a_key():
    store = KeyStore(parse_api_keys("portal:portal-key-0123456789,smoke:smoke-key-012"))
    assert store.client_for("portal-key-0123456789") == "portal"
    assert store.client_for("smoke-key-012") == "smoke"
    for wrong in ("portal-key-012345678", "PORTAL-KEY-0123456789", "", None):
        assert store.client_for(wrong) is None


def test_the_rate_limiter_blocks_after_the_limit_and_says_when_to_retry():
    clock = ManualClock()
    limiter = RateLimiter(limit=2, clock=clock)
    assert limiter.hit("portal") is None
    clock.seconds = 10
    assert limiter.hit("portal") is None
    clock.seconds = 20.5
    assert limiter.hit("portal") == 40
    assert limiter.hit("smoke") is None


def test_the_rate_limiter_lets_the_client_back_when_the_window_moves():
    clock = ManualClock()
    limiter = RateLimiter(limit=1, clock=clock)
    assert limiter.hit("portal") is None
    clock.seconds = 59.9
    assert limiter.hit("portal") == 1
    clock.seconds = 60.0
    assert limiter.hit("portal") is None
