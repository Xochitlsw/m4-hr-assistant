"""Pasos de los escenarios de ``features/observability.feature`` (pytest-bdd)."""

import json
import uuid

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/observability.feature")

QUESTION = "¿Quién es EMP-0004?"


class BrokenModel:
    """Modelo que siempre falla, para provocar un error interno."""

    def complete(self, messages):
        raise RuntimeError(f"model failed with {len(messages)} messages: secret detail")


@given("the HR Assistant with a broken model", target_fixture="api")
def broken_api(make_client):
    return make_client(model=BrokenModel())


@when("I check the health endpoint", target_fixture="response")
def check_health(api):
    return api.get("/health")


@when(
    parsers.parse('I ask "{question}" with the request id "{request_id}"'),
    target_fixture="response",
)
def ask_with_request_id(api, question, request_id):
    return api.post(
        "/ask", json={"question": question}, headers={"X-Request-ID": request_id}
    )


@when("I read the metrics", target_fixture="metrics")
def read_metrics(api):
    return api.get("/metrics").json()


@then("the response carries a UUID request id")
def carries_uuid(response):
    assert uuid.UUID(response.headers["X-Request-ID"])


@then(parsers.parse('the header and the body carry the request id "{request_id}"'))
def carries_request_id(response, request_id):
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["request_id"] == request_id


@then(parsers.parse('the log events for "{request_id}" are "{events}"'))
def log_events(log_records, request_id, events):
    found = [r["event"] for r in log_records() if r["request_id"] == request_id]
    assert found == events.split(",")


@then(parsers.parse('the audit line records the tool "{tool}" for "{employee_id}"'))
def audit_line(log_records, tool, employee_id):
    audit = next(r for r in log_records() if r["event"] == "audit")
    assert (audit["action"], audit["tool"]) == ("tool_call", tool)
    assert audit["args"] == {"employee_id": employee_id}


@then("the log never contains the question or the answer")
def log_without_text(log_records, response):
    text = json.dumps(log_records(), ensure_ascii=False)
    assert QUESTION not in text
    assert response.json()["answer"] not in text
    assert "Jorge Díaz" not in text


@then(parsers.parse('the body is the generic internal error for "{request_id}"'))
def generic_error(response, request_id):
    assert response.json() == {"detail": "internal error", "request_id": request_id}
    assert response.headers["X-Request-ID"] == request_id


@then(
    parsers.parse(
        'the error line for "{request_id}" has only the error type "{error_type}"'
    )
)
def error_line(log_records, request_id, error_type):
    errors = [r for r in log_records() if r["event"] == "error"]
    error = next(r for r in errors if r["request_id"] == request_id)
    assert error["error_type"] == error_type
    assert "secret detail" not in json.dumps(error)


@then(
    parsers.parse(
        "the metrics count {requests:d} request with p50 and p95 of {latency:g} ms"
    )
)
def metrics_count(metrics, requests, latency):
    assert metrics["requests"] == requests
    assert metrics["latency_ms"] == {"p50": latency, "p95": latency}
    assert metrics["error_rate"] == 0.0


@then("the metrics report the tokens of that answer")
def metrics_tokens(metrics, response):
    body = response.json()
    expected = {"input": body["input_tokens"], "output": body["output_tokens"]}
    assert metrics["tokens"] == expected
    assert expected["input"] > 0


@then("the SLO is met")
def slo_met(metrics):
    assert metrics["slo"] == {"p95_ms_target": 2000, "met": True}
