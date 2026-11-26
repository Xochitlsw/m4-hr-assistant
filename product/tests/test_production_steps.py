"""Pasos de los escenarios de ``features/production.feature`` (pytest-bdd)."""

import json

from pytest_bdd import given, parsers, scenarios, then, when

from tests.conftest import TEST_CLIENT, TEST_KEY

scenarios("features/production.feature")


def log_text(log_records):
    return json.dumps(log_records(), ensure_ascii=False)


@given(
    parsers.parse("the HR Assistant limited to {limit:d} questions per minute"),
    target_fixture="api",
)
def limited_api(make_client, limit):
    return make_client(RATE_LIMIT_PER_MINUTE=str(limit))


@when(
    parsers.parse('a client asks "{question}" with the key "{key}"'),
    target_fixture="response",
)
def ask_with_key(api, question, key):
    return api.post("/ask", json={"question": question}, headers={"X-API-Key": key})


@when(
    parsers.parse('a client asks "{question}" without a key'),
    target_fixture="response",
)
def ask_without_key(api, question):
    return api.post("/ask", json={"question": question}, headers={"X-API-Key": ""})


@when(
    parsers.parse('the client asks "{question}" with its key'),
    target_fixture="response",
)
def ask_with_its_key(api, question):
    return api.post("/ask", json={"question": question})


@when(
    "a visitor opens the page and the health check without a key",
    target_fixture="responses",
)
def open_without_key(api):
    without_key = {"X-API-Key": ""}
    return [api.get(path, headers=without_key) for path in ("/", "/health")]


@when(
    parsers.parse('the client asks "{question}" {times:d} times'),
    target_fixture="responses",
)
def ask_many_times(api, question, times):
    return [api.post("/ask", json={"question": question}) for _ in range(times)]


@then("the body says the key is invalid or missing")
def invalid_key_body(response):
    assert response.json() == {"detail": "invalid or missing API key"}


@then(parsers.parse('the audit records "{action}" because "{reason}"'))
def audit_reason(log_records, action, reason):
    audits = [r for r in log_records() if r["event"] == "audit"]
    assert [(r["action"], r["reason"]) for r in audits] == [(action, reason)]
    assert audits[0]["path"] == "/ask"


@then(parsers.parse('the log never contains "{text}"'))
def log_without(log_records, text):
    assert text not in log_text(log_records)


@then("both answer 200 and the page has the key field")
def open_answers(responses):
    assert [r.status_code for r in responses] == [200, 200]
    assert 'data-testid="api-key"' in responses[0].text


@then("the answers are 200, 200 and 429")
def limited_answers(responses):
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert responses[-1].json() == {"detail": "rate limit exceeded"}


@then("the last answer says when to retry")
def retry_after(responses):
    assert 0 < int(responses[-1].headers["Retry-After"]) <= 60


@then(parsers.parse('the audit records "{action}" for the client'))
def audit_client(log_records, action):
    audits = [r for r in log_records() if r.get("action") == action]
    assert len(audits) == 1
    assert audits[0]["client"] == TEST_CLIENT
    assert audits[0]["retry_after"] > 0


@then("every line of that request names the client")
def lines_name_client(log_records, response):
    request_id = response.headers["X-Request-ID"]
    lines = [r for r in log_records() if r["request_id"] == request_id]
    assert [r["event"] for r in lines] == ["audit", "agent_run", "request"]
    assert {r["client"] for r in lines} == {TEST_CLIENT}


@then("the log never contains the client key")
def log_without_client_key(log_records):
    assert TEST_KEY not in log_text(log_records)
