"""Origen de las peticiones, límite de claves fallidas y presupuesto diario (ADR 0017).

Las IP son de los rangos de documentación (RFC 5737): no pertenecen a nadie.
"""

import re
from datetime import datetime, timezone

import pytest

from app.config import ConfigError, load_settings
from app.security import DailyBudget, FailureLimiter, OriginFingerprint, client_origin
from tests.conftest import TEST_KEY

PROXY = "203.0.113.7"
OTHER = "203.0.113.8"
SPOOFED = "198.51.100.1"
OTHER_KEY = "other-key-0123456789"
THROTTLED = {"detail": "too many failed authentication attempts"}
OVER_BUDGET = {"detail": "daily budget exceeded"}


class ManualClock:
    """Reloj de prueba que solo avanza cuando la prueba lo pide."""

    def __init__(self, now=datetime(2026, 11, 26, 23, 59, 0, tzinfo=timezone.utc)):
        self.current = now
        self.seconds = 0.0

    def now(self):
        return self.current

    def monotonic(self):
        return self.seconds


def audits(log_records, action):
    return [
        record
        for record in log_records()
        if record["event"] == "audit" and record["action"] == action
    ]


@pytest.mark.parametrize(
    "forwarded, peer, hops, expected",
    [
        (None, "10.0.0.5", 0, "10.0.0.5"),
        (f"{SPOOFED}, {PROXY}", "10.0.0.5", 0, "10.0.0.5"),
        (f"{SPOOFED}, {PROXY}", "10.0.0.5", 1, PROXY),
        (f"{SPOOFED}, {PROXY}", "10.0.0.5", 2, SPOOFED),
        (PROXY, "10.0.0.5", 2, "10.0.0.5"),
        (None, "10.0.0.5", 1, "10.0.0.5"),
        (None, None, 0, "unknown"),
    ],
)
def test_the_origin_is_the_value_added_by_the_trusted_proxy(
    forwarded, peer, hops, expected
):
    assert client_origin(forwarded, peer, hops) == expected


def test_the_origin_fingerprint_hides_the_ip_and_changes_per_process():
    first, second = OriginFingerprint(), OriginFingerprint()
    assert first(PROXY) == first(PROXY) != first(OTHER)
    assert first(PROXY) != second(PROXY)
    assert re.fullmatch(r"[0-9a-f]{16}", first(PROXY))
    assert PROXY not in first(PROXY)


def test_failures_block_an_origin_only_inside_the_window():
    clock = ManualClock()
    limiter = FailureLimiter(2, clock)
    limiter.record(PROXY)
    assert limiter.retry_after(PROXY) is None
    limiter.record(PROXY)
    assert limiter.retry_after(PROXY) == 60
    assert limiter.retry_after(OTHER) is None
    clock.seconds = 60.0
    assert limiter.retry_after(PROXY) is None


def test_the_failure_limiter_forgets_quiet_origins_to_bound_memory():
    clock = ManualClock()
    limiter = FailureLimiter(5, clock, max_origins=2)
    limiter.record(PROXY)
    limiter.record(OTHER)
    clock.seconds = 61.0
    limiter.record(SPOOFED)
    assert limiter.tracked == 1


def test_the_budget_resets_at_utc_midnight():
    clock = ManualClock()
    budget = DailyBudget(0.001, clock)
    budget.add("portal", 0.0006)
    assert budget.retry_after("portal") is None
    budget.add("portal", 0.0006)
    assert budget.retry_after("portal") == 60
    assert budget.retry_after("ops") is None
    clock.current = datetime(2026, 11, 27, 0, 0, 1, tzinfo=timezone.utc)
    assert budget.retry_after("portal") is None


def test_a_zero_budget_never_blocks():
    budget = DailyBudget(0.0, ManualClock())
    budget.add("portal", 99.0)
    assert budget.retry_after("portal") is None


def test_access_settings_are_validated_at_startup():
    settings = load_settings({}).security
    assert (
        settings.auth_failures_per_minute,
        settings.trusted_proxy_hops,
        settings.daily_budget_usd,
    ) == (10, 0, 1.0)
    invalid = [
        ("AUTH_FAILURES_PER_MINUTE", "0"),
        ("TRUSTED_PROXY_HOPS", "-1"),
        ("CLIENT_DAILY_BUDGET_USD", "-1"),
        ("CLIENT_DAILY_BUDGET_USD", "mucho"),
    ]
    for name, value in invalid:
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})


def test_too_many_failed_keys_throttle_the_origin_before_comparing(
    make_client, log_records, log_stream
):
    client = make_client(AUTH_FAILURES_PER_MINUTE="3", TRUSTED_PROXY_HOPS="1")
    wrong = {"X-API-Key": "wrong-key-0123456789", "X-Forwarded-For": PROXY}
    statuses = [
        client.post("/ask", json={"question": "Hola"}, headers=wrong).status_code
        for _ in range(3)
    ]
    blocked = client.post("/ask", json={"question": "Hola"}, headers=wrong)
    right = client.post(
        "/ask",
        json={"question": "Hola"},
        headers={"X-API-Key": TEST_KEY, "X-Forwarded-For": PROXY},
    )
    assert statuses == [401, 401, 401]
    for response in (blocked, right):
        assert (response.status_code, response.json()) == (429, THROTTLED)
        assert response.headers["Retry-After"] == "60"
    failed = audits(log_records, "auth_failed")
    [throttled, _] = audits(log_records, "auth_throttled")
    assert {record["origin"] for record in failed} == {throttled["origin"]}
    assert PROXY not in log_stream.getvalue()


def test_each_origin_has_its_own_failure_count(make_client):
    client = make_client(AUTH_FAILURES_PER_MINUTE="1", TRUSTED_PROXY_HOPS="1")

    def ask(forwarded):
        headers = {"X-API-Key": "wrong-key-0123456789", "X-Forwarded-For": forwarded}
        return client.post("/ask", json={"question": "Hola"}, headers=headers)

    assert ask(PROXY).status_code == 401
    assert ask(f"{SPOOFED}, {PROXY}").status_code == 429
    assert ask(OTHER).status_code == 401


def test_a_client_over_its_daily_budget_gets_429(make_client, log_records):
    client = make_client(
        CLIENT_DAILY_BUDGET_USD="0.00002",
        API_KEYS=f"tests:{TEST_KEY},other:{OTHER_KEY}",
    )
    question = {"question": "¿Quién es EMP-0004?"}
    statuses = [client.post("/ask", json=question).status_code for _ in range(2)]
    over = client.post("/ask", json=question)
    other = client.post("/ask", json=question, headers={"X-API-Key": OTHER_KEY})
    assert statuses == [200, 200]
    assert (over.status_code, over.json()) == (429, OVER_BUDGET)
    assert over.headers["Retry-After"] == str(12 * 60 * 60)
    assert other.status_code == 200
    [record] = audits(log_records, "budget_exceeded")
    assert (record["client"], record["budget_usd"]) == ("tests", 0.00002)
