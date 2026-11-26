"""Pruebas de punta a punta de la página, con Playwright y el modelo simulado.

La verificación ``e2e`` arranca el producto y pasa ``--base-url``; por eso cada
prueba abre ``/``.
"""

from playwright.sync_api import Page, expect


def ask(page: Page, question: str) -> None:
    """Escribe una pregunta y la envía, como lo haría una persona."""
    page.get_by_test_id("question").fill(question)
    page.get_by_test_id("send").click()


def test_asking_a_question_shows_the_answer_and_its_support_id(page: Page):
    page.goto("/")
    with page.expect_response("**/ask") as answered:
        ask(page, "¿Quién es EMP-0004?")
    request_id = answered.value.json()["request_id"]
    conversation = page.get_by_test_id("answer")
    expect(conversation).to_contain_text("Jorge Díaz")
    expect(conversation).to_contain_text(f"Id de soporte: {request_id}")


def test_an_empty_question_shows_an_error_in_spanish(page: Page):
    page.goto("/")
    page.get_by_test_id("send").click()
    expect(page.get_by_test_id("answer")).to_contain_text(
        "Escribe una pregunta antes de enviar."
    )


def test_a_second_question_keeps_the_previous_conversation(page: Page):
    page.goto("/")
    conversation = page.get_by_test_id("answer")
    ask(page, "¿Quién es EMP-0004?")
    expect(conversation).to_contain_text("Jorge Díaz")
    with page.expect_request("**/ask") as second:
        ask(page, "¿Quiénes trabajan en Finanzas?")
    expect(conversation).to_contain_text("Sofía Gómez")
    expect(conversation).to_contain_text("Jorge Díaz")
    assert len(second.value.post_data_json["history"]) == 2
