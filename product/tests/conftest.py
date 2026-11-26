"""Fixtures compartidas: la aplicación armada con ``create_app()`` y sus piezas.

Las pruebas no dependen de las variables de entorno del proceso: cada una arma su
configuración con ``load_settings()`` a partir de un diccionario.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.directory import EmployeeDirectory
from app.main import create_app

PRODUCT = Path(__file__).resolve().parents[1]
EMPLOYEES = PRODUCT / "data" / "employees.json"


@pytest.fixture
def make_client():
    """Fábrica de clientes HTTP con la configuración que pida la prueba."""

    def build(**env: str) -> TestClient:
        return TestClient(create_app(settings=load_settings(env)))

    return build


@pytest.fixture
def client(make_client):
    """Cliente HTTP con la configuración por omisión y el modelo simulado."""
    return make_client()


@pytest.fixture
def directory():
    """El directorio de empleados con los datos del producto."""
    return EmployeeDirectory.load(EMPLOYEES)
