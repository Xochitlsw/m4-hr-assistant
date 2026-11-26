"""``scripts/security_check.py`` contra la aplicación, sin red (ADR 0017).

La misma comprobación que se corre contra Render se corre aquí sobre ``TestClient``,
que es un ``httpx.Client``: cada caso de ataque recibe el código esperado.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_KEY

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "security_check.py"


@pytest.fixture(name="check")
def check_module():
    """``scripts/security_check.py`` cargado por su ruta (``scripts`` no es paquete)."""
    spec = importlib.util.spec_from_file_location("security_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_attack_case_gets_the_expected_status(check, make_client):
    app = make_client().app
    results = check.run_checks(TestClient(app), TEST_KEY)
    assert len(results) == 13
    assert [name for name, expected, received in results if expected != received] == []


def test_the_check_takes_the_key_only_from_the_environment(check, monkeypatch, capsys):
    monkeypatch.delenv("SMOKE_API_KEY", raising=False)
    assert check.main(["https://hr.example"]) == 2
    monkeypatch.setenv("SMOKE_API_KEY", TEST_KEY)
    assert check.main(["https://hr.example", TEST_KEY]) == 2
    assert TEST_KEY not in capsys.readouterr().out
