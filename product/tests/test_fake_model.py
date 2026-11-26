"""Pruebas del modelo simulado: sus reglas y su conteo de tokens."""

import json

from app.llm import FakeModel

DEPARTMENTS = ["Tecnología", "Ventas", "Finanzas"]


def reply_to(content):
    model = FakeModel(DEPARTMENTS)
    completion = model.complete([{"role": "user", "content": content}])
    return json.loads(completion.text)


def test_a_tool_result_becomes_an_answer():
    reply = reply_to("Resultado de la herramienta: Ana López")
    assert reply == {"answer": "Según el directorio: Ana López"}


def test_an_employee_id_wins_over_a_department():
    reply = reply_to("¿EMP-0004 trabaja en Finanzas?")
    assert reply == {"tool": "get_employee", "args": {"employee_id": "EMP-0004"}}


def test_a_department_is_found_without_case_or_accents():
    reply = reply_to("¿Quiénes están en tecnologia?")
    assert reply == {
        "tool": "search_by_department",
        "args": {"department": "Tecnología"},
    }


def test_tokens_are_counted_in_words():
    messages = [
        {"role": "system", "content": "uno dos"},
        {"role": "user", "content": "¿Qué día es hoy?"},
    ]
    completion = FakeModel(DEPARTMENTS).complete(messages)
    assert completion.input_tokens == 6
    assert completion.output_tokens == len(completion.text.split())
