"""Pruebas de la configuración: valores por omisión y validación al arrancar."""

from pathlib import Path

import pytest

from app.config import ConfigError, load_settings
from app.main import create_app

PRODUCT = Path(__file__).resolve().parents[1]


def test_defaults_come_from_the_spec():
    settings = load_settings({})
    assert settings.model_provider == "fake"
    assert settings.agent_max_steps == 3
    assert settings.max_question_chars == 2000
    assert settings.employees_path == PRODUCT / "data" / "employees.json"


def test_invalid_values_stop_the_start_with_a_clear_message():
    invalid = [
        ("AGENT_MAX_STEPS", "0"),
        ("AGENT_MAX_STEPS", "many"),
        ("MAX_QUESTION_CHARS", "-5"),
        ("MODEL_PROVIDER", "gpt"),
    ]
    for name, value in invalid:
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})


def test_a_missing_employees_file_stops_the_start(tmp_path):
    settings = load_settings({"EMPLOYEES_PATH": str(tmp_path / "missing.json")})
    with pytest.raises(ConfigError, match="EMPLOYEES_PATH"):
        create_app(settings=settings)
