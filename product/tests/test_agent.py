"""Pruebas del agente: bucle con tope, protocolo JSON y tokens."""

import json

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
