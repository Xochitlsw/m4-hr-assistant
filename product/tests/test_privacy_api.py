"""Datos personales y secretos a través de la API (ADR 0014).

Entrada: los datos personales de la pregunta y del historial se redactan antes de
llegar al modelo; un secreto bloquea la petición con ``400``. Salida: la respuesta se
redacta. La auditoría dice qué tipos y cuántos, dónde, nunca el valor. Los datos del
directorio (nombre, puesto, departamento, fecha de ingreso y estado) no se redactan.
"""

import json

import pytest

from app.llm import Completion

CURP = "XAXX010101HDFXXX09"
EMAIL = "persona.prueba@example.com"
SECRET = "lsv2" + "_pt_" + "b2" * 16
BLOCKED = {"detail": "request contains a secret; remove it and ask again"}


class RecordingModel:
    """Modelo de prueba: guarda los mensajes que recibe y responde con ``answer``.

    :param answer: Texto de la respuesta, en el protocolo
    """

    def __init__(self, answer="Listo"):
        self.answer = answer
        self.calls = []

    def complete(self, messages):
        self.calls.append([dict(message) for message in messages])
        return Completion(json.dumps({"answer": self.answer}), 1, 1)

    def seen(self):
        return json.dumps(self.calls, ensure_ascii=False)


def audits(log_records, action):
    return [
        record
        for record in log_records()
        if record["event"] == "audit" and record["action"] == action
    ]


def test_personal_data_in_the_question_never_reaches_the_model(
    make_client, log_records, log_stream
):
    model = RecordingModel()
    client = make_client(model=model)
    question = f"Mi CURP es {CURP} y mi correo {EMAIL}, ¿quién es EMP-0004?"
    assert client.post("/ask", json={"question": question}).status_code == 200
    assert CURP not in model.seen() and EMAIL not in model.seen()
    assert "[REDACTED:CURP]" in model.seen() and "EMP-0004" in model.seen()
    [record] = audits(log_records, "pii_detected")
    assert (record["location"], record["types"]) == (
        "question",
        {"CURP": 1, "EMAIL": 1},
    )
    assert CURP not in log_stream.getvalue() and EMAIL not in log_stream.getvalue()


def test_personal_data_in_the_history_is_redacted_too(make_client, log_records):
    model = RecordingModel()
    history = [
        {"role": "user", "content": f"Escríbeme a {EMAIL}"},
        {"role": "assistant", "content": "De acuerdo."},
    ]
    response = make_client(model=model).post(
        "/ask", json={"question": "¿Quién es EMP-0004?", "history": history}
    )
    assert response.status_code == 200
    assert EMAIL not in model.seen()
    [record] = audits(log_records, "pii_detected")
    assert (record["location"], record["types"]) == ("history", {"EMAIL": 1})


@pytest.mark.parametrize("where", ["question", "history"])
def test_a_secret_blocks_the_request_before_the_model(
    make_client, log_records, log_stream, where
):
    model = RecordingModel()
    body = {"question": "¿Quién es EMP-0004?"}
    if where == "question":
        body["question"] = f"Mi clave de LangSmith es {SECRET}"
    else:
        body["history"] = [{"role": "user", "content": f"token {SECRET}"}]
    response = make_client(model=model).post("/ask", json=body)
    assert (response.status_code, response.json()) == (400, BLOCKED)
    assert response.headers["X-Request-ID"]
    assert not model.calls
    [record] = audits(log_records, "secret_blocked")
    assert (record["location"], record["types"]) == (where, {"LANGSMITH_KEY": 1})
    assert SECRET not in log_stream.getvalue()


def test_personal_data_in_the_answer_is_redacted(make_client, log_records):
    model = RecordingModel(f"Jorge Díaz; su correo es {EMAIL}")
    response = make_client(model=model).post(
        "/ask", json={"question": "¿Quién es EMP-0004?"}
    )
    assert response.json()["answer"] == "Jorge Díaz; su correo es [REDACTED:EMAIL]"
    [record] = audits(log_records, "pii_detected")
    assert (record["location"], record["types"]) == ("answer", {"EMAIL": 1})


def test_directory_answers_are_never_redacted(client, log_records, directory):
    everyone = [
        employee
        for department in directory.departments()
        for employee in directory.search_by_department(department)
    ]
    assert len(everyone) >= 5
    for employee in everyone:
        body = client.post(
            "/ask", json={"question": f"¿Quién es {employee['id']}?"}
        ).json()
        for field in ("name", "role", "department", "start_date", "status"):
            assert employee[field] in body["answer"]
    assert not audits(log_records, "pii_detected")
