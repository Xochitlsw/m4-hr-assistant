"""Pasos de los escenarios de ``features/costs.feature`` (pytest-bdd)."""

from pytest_bdd import given, parsers, scenarios, then, when

from tests.conftest import GATEWAY_ENV, LARGE, SMALL, FakeGateway

scenarios("features/costs.feature")

MODELS = {"small": SMALL, "large": LARGE}


@given("the HR Assistant with routing off", target_fixture="api")
def routing_off_api(make_client):
    return make_client(ROUTING="false")


@given(
    "the HR Assistant on a gateway that answers from its cache",
    target_fixture="api",
)
def cached_gateway_api(make_client):
    gateway = FakeGateway(cache_status="HIT")
    return make_client(http_client=gateway.client(), **GATEWAY_ENV)


@given("the HR Assistant on a gateway that is down", target_fixture="api")
def down_gateway_api(make_client):
    gateway = FakeGateway(status=503)
    return make_client(http_client=gateway.client(), **GATEWAY_ENV)


@when(
    parsers.parse('I ask "{question}" as request "{request_id}"'),
    target_fixture="response",
)
def ask_as_request(api, question, request_id):
    return api.post(
        "/ask", json={"question": question}, headers={"X-Request-ID": request_id}
    )


@then(parsers.parse('the answer comes from the {size} model because "{route}"'))
def answer_model(response, size, route):
    body = response.json()
    assert (body["model"], body["route"]) == (MODELS[size], route)


@then("the answer has a cost above zero")
def answer_cost(response):
    body = response.json()
    assert body["cost_usd"] > 0
    assert body["cached_calls"] == 0


@then(parsers.parse("the answer has {calls:d} cached calls and costs nothing"))
def answer_cached(response, calls):
    body = response.json()
    assert (body["cached_calls"], body["cost_usd"]) == (calls, 0)
    assert body["input_tokens"] > 0


@then(parsers.parse('the body says the model is unavailable for "{request_id}"'))
def model_unavailable(response, request_id):
    expected = {"detail": "model unavailable", "request_id": request_id}
    assert response.json() == expected
    assert response.headers["X-Request-ID"] == request_id


@then(parsers.parse('the error line for "{request_id}" names "{error_type}"'))
def error_line(log_records, request_id, error_type):
    errors = [r for r in log_records() if r["event"] == "error"]
    assert [r["error_type"] for r in errors if r["request_id"] == request_id] == [
        error_type
    ]
    assert "test-only-token" not in str(log_records())
