"""Secretos: plantilla sin valores y ninguna instrucción con una clave en un comando.

Una clave escrita en la línea de comandos queda en el historial de la consola (en
PowerShell, en texto plano). Por eso los ejemplos la leen sin eco o desde ``.env``, y
``scripts/smoke.py`` solo la acepta en la variable ``SMOKE_API_KEY``. Si alguna prueba
falla, el mensaje nombra variables y archivos, nunca un valor.
"""

import importlib.util
import re
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = PRODUCT / ".env.example"
SMOKE = PRODUCT / "scripts" / "smoke.py"
SECRETS = ("API_KEYS", "CF_API_TOKEN", "LANGSMITH_API_KEY", "SMOKE_API_KEY")
SECRET_SUFFIXES = ("_KEY", "_KEYS", "_TOKEN", "_SECRET", "_PASSWORD")
# Una variable secreta con un valor literal o un marcador (<clave>) en un comando.
KEY_IN_COMMAND = re.compile(
    r"(?:\$env:)?\b(?:" + "|".join(SECRETS) + r")\s*=\s*['\"<]"
)
SMOKE_KEY = "smoke-key-0123456789"


def template_values() -> dict[str, str]:
    """Variables de ``.env.example`` con su valor, sin comentarios ni líneas vacías."""
    values = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def instruction_files() -> list[Path]:
    """README, ``doc/`` y la prueba de humo: los archivos con instrucciones."""
    return [PRODUCT / "README.md", SMOKE, *sorted((PRODUCT / "doc").rglob("*.md"))]


@pytest.fixture(name="smoke")
def smoke_module():
    """``scripts/smoke.py`` cargado por su ruta (``scripts`` no es un paquete)."""
    spec = importlib.util.spec_from_file_location("smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_template_lists_every_secret():
    assert set(SECRETS) <= set(template_values())


def test_every_secret_in_the_template_is_empty():
    filled = sorted(
        name
        for name, value in template_values().items()
        if name.endswith(SECRET_SUFFIXES) and value
    )
    assert filled == []


def test_no_instruction_puts_a_key_inside_a_command():
    found = [
        f"{path.relative_to(PRODUCT).as_posix()}:{number}"
        for path in instruction_files()
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if KEY_IN_COMMAND.search(line)
    ]
    assert found == []


def test_the_smoke_usage_never_shows_the_key_inside_the_command(
    smoke, monkeypatch, capsys
):
    monkeypatch.delenv("SMOKE_API_KEY", raising=False)
    assert smoke.main(["https://hr.example"]) == 2
    usage = capsys.readouterr().out
    assert "SMOKE_API_KEY" in usage
    assert not KEY_IN_COMMAND.search(usage)
    assert "SMOKE_API_KEY=" not in usage


def test_the_smoke_test_rejects_a_key_given_as_an_argument(smoke, monkeypatch, capsys):
    monkeypatch.setenv("SMOKE_API_KEY", SMOKE_KEY)
    assert smoke.main(["https://hr.example", SMOKE_KEY]) == 2
    assert SMOKE_KEY not in capsys.readouterr().out
