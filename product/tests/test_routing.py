"""Pruebas de las palancas 1 y 2: el enrutador por regla y la ventana de historial."""

from app.config import load_settings
from app.routing import Router, apply_window
from tests.conftest import LARGE, SMALL


def router(**env):
    settings = load_settings(env)
    return Router(settings.routing, settings.models.small, settings.models.large)


def test_keywords_go_to_the_large_model_without_case_or_accents():
    choose = router().choose
    assert choose("Explícame quién es EMP-0004") == (LARGE, "keyword:explica")
    assert choose("¿POR QUÉ está de baja EMP-0003?") == (LARGE, "keyword:por que")
    assert choose("Comparación de Ventas y Finanzas") == (LARGE, "keyword:compara")


def test_long_questions_and_routing_off_go_to_the_large_model():
    assert router().choose("a" * 81) == (LARGE, "long_question")
    assert router().choose("a" * 80) == (SMALL, "simple")
    assert router().choose("¿Quién es EMP-0004?") == (SMALL, "simple")
    off = router(ROUTING="false")
    assert off.choose("¿Quién es EMP-0004?") == (LARGE, "routing_off")


def test_the_window_keeps_only_the_latest_messages():
    history = [{"role": "user", "content": str(number)} for number in range(10)]
    assert [turn["content"] for turn in apply_window(history, 3)] == ["7", "8", "9"]
    assert not apply_window(history, 0)
    assert apply_window(history, 1000) == history
