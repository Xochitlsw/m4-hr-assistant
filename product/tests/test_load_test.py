"""``scripts/load_test.py`` contra la aplicación en memoria (ASGI), sin red (ADR 0019).

La misma prueba de carga que se corre contra uvicorn o Render corre aquí sobre
``httpx.ASGITransport``: cuenta por código, mide percentiles y muestra que el límite
por cliente protege al servicio.
"""

import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest

from tests.conftest import TEST_KEY

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "load_test.py"


@pytest.fixture(name="load")
def load_module():
    """``scripts/load_test.py`` cargado por su ruta (``scripts`` no es paquete)."""
    spec = importlib.util.spec_from_file_location("load_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(load, app, requests, concurrency):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await load.run_load(client, TEST_KEY, requests, concurrency)

    return asyncio.run(go())


def test_concurrent_requests_all_succeed_and_are_measured(load, make_client):
    app = make_client(RATE_LIMIT_PER_MINUTE="1000").app
    summary = run(load, app, requests=40, concurrency=10)
    assert summary["by_status"] == {200: 40}
    assert summary["network_errors"] == 0
    assert 0 < summary["p50_ms"] <= summary["p95_ms"] <= summary["max_ms"]
    assert summary["requests_per_second"] > 0


def test_the_rate_limit_protects_the_service_under_load(load, make_client):
    app = make_client(RATE_LIMIT_PER_MINUTE="10").app
    summary = run(load, app, requests=30, concurrency=10)
    assert summary["by_status"] == {200: 10, 429: 20}


def test_the_report_lists_counts_and_latencies(load):
    summary = {
        "requests": 3,
        "concurrency": 2,
        "seconds": 1.5,
        "requests_per_second": 2.0,
        "by_status": {200: 2, 429: 1},
        "network_errors": 0,
        "p50_ms": 10.0,
        "p95_ms": 20.0,
        "max_ms": 21.0,
    }
    report = load.format_report(summary)
    assert "200: 2" in report and "429: 1" in report
    assert "p95 20.0 ms" in report
