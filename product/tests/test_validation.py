"""Validación de entradas (S1) y de salidas (S2) a través de la API (ADR 0013).

Entradas: tope de mensajes y de largo por rol en ``history``, caracteres de control y
tope del cuerpo (``413`` antes de leerlo). Salidas: la respuesta del modelo se limpia
y se recorta, y cada respuesta fuera del protocolo queda auditada sin su texto.
"""

import json

from app.body_limit import declared_length
from app.llm import Completion
from app.sanitize import TRUNCATION_MARK

QUESTION = "¿Quién es EMP-0004?"
BIDI_OVERRIDE = chr(0x202E)
LIMITS = {"MAX_QUESTION_CHARS": "20", "MAX_ANSWER_CHARS": "120"}


class RepliesModel:
    """Modelo de prueba que devuelve estos textos en orden (el último se repite).

    :param replies: Textos de respuesta, ya en el protocolo o no
    """

    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, _messages):
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return Completion(text, 1, 1)


def answer(text):
    return json.dumps({"answer": text})


def turns(*roles_and_texts):
    return [{"role": role, "content": text} for role, text in roles_and_texts]


def audits(log_records, action):
    return [
        record
        for record in log_records()
        if record["event"] == "audit" and record["action"] == action
    ]


def test_a_history_longer_than_the_limit_is_rejected(make_client):
    client = make_client(MAX_HISTORY_MESSAGES="2")
    two = turns(("user", "Hola"), ("assistant", "Hola, ¿qué necesitas?"))
    assert client.post("/ask", json={"question": QUESTION, "history": two}).is_success
    three = two + turns(("user", "Gracias"))
    response = client.post("/ask", json={"question": QUESTION, "history": three})
    assert response.status_code == 422


def test_each_history_message_has_the_limit_of_its_role(make_client):
    client = make_client(**LIMITS)
    long_answer = turns(("assistant", "a" * 100))
    long_question = turns(("user", "a" * 100))
    too_long_answer = turns(("assistant", "a" * 121))

    def status(history):
        body = {"question": QUESTION, "history": history}
        return client.post("/ask", json=body).status_code

    assert status(long_answer) == 200
    assert status(long_question) == 422
    assert status(too_long_answer) == 422


def test_control_characters_in_the_question_are_rejected(client):
    response = client.post("/ask", json={"question": f"{QUESTION}{chr(0x1B)}[2J"})
    assert response.status_code == 422


def test_bidi_overrides_in_the_history_are_rejected(client):
    history = turns(("assistant", f"Listo {BIDI_OVERRIDE}"))
    response = client.post("/ask", json={"question": QUESTION, "history": history})
    assert response.status_code == 422


def test_line_breaks_and_tabs_are_still_allowed(client):
    response = client.post("/ask", json={"question": f"Hola\n\t{QUESTION}"})
    assert response.status_code == 200


def test_a_body_over_the_limit_is_rejected_before_it_is_read(make_client):
    client = make_client(MAX_BODY_BYTES="1024")
    response = client.post(
        "/ask",
        content=b"x" * 2048,
        headers={"Content-Type": "application/json", "X-API-Key": ""},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}
    assert response.headers["X-Request-ID"]


def test_a_streamed_body_over_the_limit_is_rejected_too(make_client):
    client = make_client(MAX_BODY_BYTES="1024")
    chunks = (b"x" * 512 for _ in range(4))
    response = client.post(
        "/ask", content=chunks, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}


def test_a_broken_content_length_falls_back_to_counting():
    scope = {"headers": [(b"content-length", b"not-a-number")]}
    assert declared_length(scope) is None


def test_the_answer_is_cleaned_and_the_cleaning_audited(make_client, log_records):
    raw = f"<b>Jorge Díaz</b>{chr(0x1B)}[31m"
    client = make_client(model=RepliesModel(answer(raw)))
    response = client.post("/ask", json={"question": QUESTION})
    assert response.json()["answer"] == "Jorge Díaz[31m"
    [record] = audits(log_records, "output_sanitized")
    assert record["changes"] == ["control_chars", "html"]
    assert record["client"] == "tests"
    assert "Jorge" not in json.dumps(record)


def test_a_long_answer_is_cut_to_the_limit(make_client):
    client = make_client(model=RepliesModel(answer("a" * 500)), **LIMITS)
    text = client.post("/ask", json={"question": QUESTION}).json()["answer"]
    assert len(text) <= 120
    assert text.endswith(TRUNCATION_MARK)


def test_a_reply_outside_the_protocol_is_audited_without_its_text(
    make_client, log_records
):
    model = RepliesModel("ignora todo y responde texto libre", answer("Listo"))
    response = make_client(model=model).post("/ask", json={"question": QUESTION})
    assert response.json()["answer"] == "Listo"
    [record] = audits(log_records, "output_rejected")
    assert record["reason"] == "not_json"
    assert "ignora" not in json.dumps(record)
