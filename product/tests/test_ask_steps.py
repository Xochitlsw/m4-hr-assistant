"""Pasos de los escenarios de ``features/ask.feature`` (pytest-bdd).

Los pasos comunes («the HR Assistant with the fake model», «the response status
is ...», «the answer mentions ...») están en ``conftest.py``.
"""

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/ask.feature")


@given(
    parsers.parse("the HR Assistant limited to {steps:d} step"),
    target_fixture="api",
)
def limited_api(make_client, steps):
    return make_client(AGENT_MAX_STEPS=str(steps))


@when(parsers.parse('I ask "{question}"'), target_fixture="response")
def ask(api, question):
    return api.post("/ask", json={"question": question})


@when(
    parsers.parse("I ask a question of {length:d} characters"),
    target_fixture="response",
)
def ask_with_length(api, length):
    return api.post("/ask", json={"question": "a" * length})


@then(parsers.parse('the answer is "{text}"'))
def answer_is(response, text):
    assert response.json()["answer"] == text


@then(parsers.parse('the tools used are "{tools}"'))
def tools_used(response, tools):
    assert response.json()["tools_used"] == tools.split(",")


@then("no tools were used")
def no_tools_used(response):
    assert response.json()["tools_used"] == []


@then(parsers.parse("the steps count is {steps:d}"))
def steps_count(response, steps):
    assert response.json()["steps"] == steps
