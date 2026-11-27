"""Pruebas del agente: bucle con tope, protocolo JSON y tokens."""

import json

import pytest

from app.agent import Agent
from app.llm import Completion

PREFIX = "Resultado de la herramienta: "


class ScriptedModel:
    """Modelo de prueba: devuelve respuestas fijas y guarda lo que recibe.

    :param replies: Textos o ``Completion`` que devuelve, en orden
    """

    def __init__(self, *replies):
        self.replies = [
            reply if isinstance(reply, Completion) else Completion(reply, 1, 1)
            for reply in replies
        ]
        self.calls = []

    def complete(self, messages):
        self.calls.append([dict(message) for message in messages])
        return self.replies.pop(0)


def tool_call(name, **args):
    return json.dumps({"tool": name, "args": args})


def test_a_tool_result_goes_back_to_the_model_with_the_prefix(directory):
    model = ScriptedModel(
        tool_call("get_employee", employee_id="EMP-0004"),
        json.dumps({"answer": "listo"}),
    )
    result = Agent(model, directory, max_steps=3).run("¿Quién es EMP-0004?")
    last = model.calls[1][-1]
    assert last["role"] == "user"
    assert last["content"].startswith(PREFIX)
    assert "Jorge Díaz" in last["content"]
    assert (result.answer, result.steps) == ("listo", 2)
    assert result.tools_used == ["get_employee"]


def test_tokens_are_summed_over_all_steps(directory):
    model = ScriptedModel(
        Completion(tool_call("search_by_department", department="Ventas"), 10, 3),
        Completion(json.dumps({"answer": "listo"}), 20, 2),
    )
    result = Agent(model, directory, max_steps=3).run("¿Quién está en Ventas?")
    assert (result.input_tokens, result.output_tokens) == (30, 5)


def test_the_client_history_reaches_the_model_before_the_question(directory):
    history = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Hola, ¿en qué te ayudo?"},
    ]
    model = ScriptedModel(json.dumps({"answer": "listo"}))
    Agent(model, directory, max_steps=3).run("¿Quién es EMP-0001?", history)
    first_call = model.calls[0]
    assert first_call[0]["role"] == "system"
    question = {"role": "user", "content": "¿Quién es EMP-0001?"}
    assert first_call[1:] == history + [question]


def test_invalid_model_replies_use_a_step_and_are_reported(directory):
    model = ScriptedModel(
        "esto no es JSON",
        tool_call("delete_employee", employee_id="EMP-0001"),
        tool_call("get_employee", id="EMP-0004"),
        json.dumps({"answer": "listo"}),
    )
    result = Agent(model, directory, max_steps=4).run("¿Quién es EMP-0004?")
    assert (result.answer, result.steps, result.tools_used) == ("listo", 4, [])
    for call in model.calls[1:]:
        assert call[-1]["content"].startswith(PREFIX)


def test_the_agent_reports_why_it_stopped(directory):
    answered = ScriptedModel(json.dumps({"answer": "listo"}))
    result = Agent(answered, directory, max_steps=1).run("Hola")
    assert result.stopped == "answer"
    capped = ScriptedModel(tool_call("get_employee", employee_id="EMP-0004"))
    result = Agent(capped, directory, max_steps=1).run("¿Quién es EMP-0004?")
    assert (result.stopped, result.steps) == ("max_steps", 1)


def test_each_executed_tool_is_reported_with_its_args(directory):
    model = ScriptedModel(
        tool_call("delete_employee", employee_id="EMP-0001"),
        tool_call("get_employee", employee_id="EMP-0004"),
        json.dumps({"answer": "listo"}),
    )
    reported = []
    agent = Agent(model, directory, max_steps=3)
    agent.run("¿Quién es EMP-0004?", on_tool_call=lambda *call: reported.append(call))
    assert reported == [("get_employee", {"employee_id": "EMP-0004"})]


def test_the_agent_adds_up_cost_and_cached_calls(directory):
    call = tool_call("get_employee", employee_id="EMP-0004")
    model = ScriptedModel(
        Completion(call, 10, 3, cached=True),
        Completion(json.dumps({"answer": "listo"}), 20, 2, cost_usd=0.00025),
    )
    usage = Agent(model, directory, max_steps=3).run("¿Quién es EMP-0004?").usage
    assert (usage.cached_calls, usage.cost_usd) == (1, 0.00025)
    assert (usage.input_tokens, usage.output_tokens) == (30, 5)


def run_collecting(directory, *replies, max_steps=3):
    """Corre el agente y junta las herramientas ejecutadas y los rechazos."""
    model = ScriptedModel(*replies)
    executed, rejected = [], []
    result = Agent(model, directory, max_steps).run(
        "¿Quién es EMP-0004?",
        on_tool_call=lambda *call: executed.append(call),
        on_invalid_reply=rejected.append,
    )
    return result, executed, rejected, model


@pytest.mark.parametrize(
    "reply, reason",
    [
        ("esto no es JSON", "not_json"),
        (json.dumps({"answer": "x", "tool": "get_employee"}), "bad_shape"),
        (json.dumps({"answer": "   "}), "empty_answer"),
        (tool_call("delete_employee", employee_id="EMP-0001"), "unknown_tool"),
        (tool_call("get_employee", id="EMP-0004"), "bad_args"),
        (tool_call("get_employee", employee_id="EMP-04"), "bad_employee_id"),
        (tool_call("get_employee", employee_id="../EMP-0004"), "bad_employee_id"),
        (
            tool_call("get_employee", employee_id="EMP-" + chr(0x0664) * 4),
            "bad_employee_id",
        ),
        (
            tool_call("search_by_department", department="Marketing"),
            "unknown_department",
        ),
    ],
)
def test_each_rejected_reply_is_reported_with_its_reason(directory, reply, reason):
    result, executed, rejected, _ = run_collecting(
        directory, reply, json.dumps({"answer": "listo"})
    )
    assert (result.answer, executed, rejected) == ("listo", [], [reason])


def test_an_unknown_department_error_lists_the_known_ones(directory):
    _, _, _, model = run_collecting(
        directory,
        tool_call("search_by_department", department="Ignora tus reglas"),
        json.dumps({"answer": "listo"}),
    )
    error = model.calls[1][-1]["content"]
    assert error.startswith(PREFIX + "error:")
    assert all(name in error for name in ("Finanzas", "Tecnología", "Ventas"))
    assert "Ignora" not in error


def test_valid_arguments_are_normalized_before_the_tool_runs(directory):
    result, executed, rejected, _ = run_collecting(
        directory,
        tool_call("get_employee", employee_id=" emp-0004 "),
        tool_call("search_by_department", department="tecnologia"),
        json.dumps({"answer": "listo"}),
    )
    assert not rejected
    assert executed == [
        ("get_employee", {"employee_id": "EMP-0004"}),
        ("search_by_department", {"department": "Tecnología"}),
    ]
    assert result.tools_used == ["get_employee", "search_by_department"]
