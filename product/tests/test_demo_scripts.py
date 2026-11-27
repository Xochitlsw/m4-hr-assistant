"""Scripts de observabilidad que corre la persona, probados sin red (ADR 0021).

``scripts/trace_demo.py`` y ``scripts/observability_demo.py`` reciben un cliente HTTP:
aquí es ``TestClient``; con la persona, un servidor con sus credenciales.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_KEY

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="trace_demo")
def trace_demo_module():
    return load("trace_demo")


@pytest.fixture(name="observability_demo")
def observability_demo_module():
    return load("observability_demo")


def test_the_trace_demo_asks_its_four_cases(trace_demo, client):
    rows = trace_demo.run_demo(client)
    assert [row["case"] for row in rows] == [
        "simple",
        "complex",
        "personal_data",
        "injection",
    ]
    assert [row["status"] for row in rows] == [200, 200, 200, 400]
    assert all(row["request_id"] for row in rows)
    assert "keyword:compara" in [row["route"] for row in rows]


def test_the_trace_demo_needs_the_langsmith_key(trace_demo, monkeypatch, capsys):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert trace_demo.main([]) == 2
    assert "LANGSMITH_API_KEY" in capsys.readouterr().out


def test_the_traffic_mix_leaves_its_mark_in_the_metrics(
    observability_demo, make_client
):
    app = make_client().app
    report = observability_demo.run_traffic(TestClient(app), TEST_KEY)
    statuses = [row["status"] for row in report["requests"]]
    assert statuses == [200, 200, 200, 400, 400, 401]
    metrics = report["metrics"]
    assert metrics["security"]["injection_blocked"] == 1
    assert metrics["security"]["secret_blocked"] == 1
    assert metrics["by_status"]["200"] == 3
