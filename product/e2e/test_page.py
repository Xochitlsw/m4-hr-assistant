"""Pruebas de punta a punta de la página, con Playwright y el modelo simulado.

La verificación ``e2e`` arranca el producto, pasa ``--base-url`` y pone la clave de
prueba en la variable ``E2E_API_KEY``; la clave nunca se escribe en estas pruebas.
"""

import os

from playwright.sync_api import Page, expect


def open_page(page: Page, key: str | None = None) -> None:
    """Abre la página y escribe la clave, como lo haría una persona."""
    page.goto("/")
    page.get_by_test_id("api-key").fill(
        os.environ["E2E_API_KEY"] if key is None else key
    )


def ask(page: Page, question: str) -> None:
    """Escribe una pregunta y la envía."""
    page.get_by_test_id("question").fill(question)
    page.get_by_test_id("send").click()


def test_asking_a_question_shows_the_answer_and_its_support_id(page: Page):
    open_page(page)
    with page.expect_response("**/ask") as answered:
        ask(page, "¿Quién es EMP-0004?")
    request_id = answered.value.json()["request_id"]
    conversation = page.get_by_test_id("answer")
    expect(conversation).to_contain_text("Jorge Díaz")
    expect(conversation).to_contain_text(f"Id de soporte: {request_id}")


def test_an_empty_question_shows_an_error_in_spanish(page: Page):
    open_page(page)
    page.get_by_test_id("send").click()
    expect(page.get_by_test_id("answer")).to_contain_text(
        "Escribe una pregunta antes de enviar."
    )


def test_a_second_question_keeps_the_previous_conversation(page: Page):
    open_page(page)
    conversation = page.get_by_test_id("answer")
    ask(page, "¿Quién es EMP-0004?")
    expect(conversation).to_contain_text("Jorge Díaz")
    with page.expect_request("**/ask") as second:
        ask(page, "¿Quiénes trabajan en Finanzas?")
    expect(conversation).to_contain_text("Sofía Gómez")
    expect(conversation).to_contain_text("Jorge Díaz")
    assert len(second.value.post_data_json["history"]) == 2


def test_a_wrong_key_shows_invalid_key(page: Page):
    open_page(page, key="not-the-right-key")
    ask(page, "¿Quién es EMP-0004?")
    expect(page.get_by_test_id("answer")).to_contain_text("Clave inválida")
