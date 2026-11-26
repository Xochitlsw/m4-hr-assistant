"""Texto que entra y sale: caracteres prohibidos y limpieza de la respuesta (ADR 0013).

Los caracteres de control se arman con ``chr()`` para que el archivo no los contenga.
"""

import pytest

from app.sanitize import TRUNCATION_MARK, has_forbidden_chars, sanitize_answer

FALLBACK = "No pude completar la respuesta."
ESC = chr(0x1B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)


@pytest.mark.parametrize(
    "code", [0x00, 0x07, 0x1B, 0x7F, 0x85, 0x202A, 0x202E, 0x2066, 0x2069]
)
def test_control_and_bidi_characters_are_forbidden(code):
    assert has_forbidden_chars(f"¿Quién es EMP-0004?{chr(code)}")


@pytest.mark.parametrize(
    "text",
    [
        "¿Quién es EMP-0004?",
        "Línea uno\nlínea dos\tcon tabulador\r\n",
        "Ñandú ingresó el 2021-05-17; salario < 5000 y > 3000",
    ],
)
def test_ordinary_text_is_allowed(text):
    assert not has_forbidden_chars(text)


def test_a_clean_answer_is_left_as_is():
    clean = sanitize_answer("Jorge Díaz trabaja en Tecnología.", 200, FALLBACK)
    assert clean.text == "Jorge Díaz trabaja en Tecnología."
    assert not clean.changes


def test_control_and_bidi_characters_are_removed_from_the_answer():
    raw = f"Jorge{ESC}[31m Díaz{RIGHT_TO_LEFT_OVERRIDE}"
    clean = sanitize_answer(raw, 200, FALLBACK)
    assert clean.text == "Jorge[31m Díaz"
    assert clean.changes == ("control_chars",)


def test_html_tags_are_removed_but_comparisons_stay():
    raw = "<script>alert(1)</script>Ana <b>López</b>: salario < 5000 y > 3000"
    clean = sanitize_answer(raw, 200, FALLBACK)
    assert clean.text == "alert(1)Ana López: salario < 5000 y > 3000"
    assert clean.changes == ("html",)


def test_a_long_answer_is_cut_and_marked():
    clean = sanitize_answer("a" * 500, 120, FALLBACK)
    assert len(clean.text) <= 120
    assert clean.text.endswith(TRUNCATION_MARK)
    assert clean.changes == ("truncated",)


def test_an_answer_left_empty_becomes_the_fallback():
    clean = sanitize_answer(f"<b></b> {ESC}", 200, FALLBACK)
    assert clean.text == FALLBACK
    assert clean.changes == ("control_chars", "html", "empty")
