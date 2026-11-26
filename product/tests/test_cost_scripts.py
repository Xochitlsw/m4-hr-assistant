"""Scripts de costos, probados sin red (ADR 0022).

``scripts/cost_ablation.py`` mide cada palanca con el modelo simulado y la misma regla
de ``verification evaluate``. ``scripts/cost_real_sample.py`` lo corre la persona con
el modelo real; aquí corre contra un AI Gateway falso que guarda en caché por
``cf-aig-cache-key``, como el real.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.conftest import GATEWAY_ENV, FakeGateway

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    """Carga un script por su ruta (``scripts`` no es paquete)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="ablation", scope="module")
def ablation_module():
    return load("cost_ablation")


@pytest.fixture(name="sample", scope="module")
def sample_module():
    load("cost_ablation")
    return load("cost_real_sample")


@pytest.fixture(name="results", scope="module")
def ablation_results(ablation):
    return {config.name: result for config, result in ablation.run_ablation()}


class CachingGateway(FakeGateway):
    """AI Gateway falso con caché: ``HIT`` si la clave ya se guardó con una TTL."""

    def __init__(self):
        super().__init__()
        self.stored = set()

    def handle(self, request):
        key = request.headers.get("cf-aig-cache-key")
        cacheable = "cf-aig-cache-ttl" in request.headers and key is not None
        self.cache_status = "HIT" if cacheable and key in self.stored else "MISS"
        if cacheable:
            self.stored.add(key)
        return super().handle(request)


def test_each_lever_saves_what_it_should(results):
    baseline = results["baseline"]
    window, routing = results["window"], results["routing"]
    assert window.input_tokens < baseline.input_tokens
    assert window.cost_usd < baseline.cost_usd
    assert routing.input_tokens == baseline.input_tokens
    assert routing.cost_usd < baseline.cost_usd
    production = results["production"]
    assert production.cost_usd < min(window.cost_usd, routing.cost_usd)
    assert production.quality == baseline.quality == 1.0


def test_the_ablation_measures_like_the_factory_judge(ablation):
    """Mismos casos, historial y regla de calidad que ``verification evaluate``."""
    judge = ablation.judge()
    for name, env in (("baseline", ablation.BASELINE), ("production", {})):
        with ablation.app_client({**ablation.FAKE_ENV, **env}) as client:
            ours = ablation.measure(client, judge)
            theirs = judge.measure(client, judge.cases)
        assert (ours.quality, ours.input_tokens) == (
            theirs["quality"],
            theirs["input_tokens"],
        ), name
        assert ours.cost_usd == pytest.approx(theirs["cost_usd"])


def test_discarded_levers_would_save_more_and_are_reported_as_discarded(
    ablation, results
):
    production = results["production"]
    assert results["window_0"].cost_usd < production.cost_usd
    assert results["all_small"].routes == {"routing_off": 8}
    report = ablation.render(ablation.run_ablation())
    assert "descartada" in report
    assert "| Ambas (producción) |" in report


def test_the_ablation_report_has_numbers_and_no_conversation_text(ablation, tmp_path):
    output = tmp_path / "cost-ablation.md"
    assert ablation.main(output) == 0
    report = output.read_text(encoding="utf-8")
    assert "USD por 1 000 peticiones" in report
    for text in ("EMP-0004", "Jorge", "Finanzas", "Monterrey"):
        assert text not in report


def test_projection_scales_the_cost_per_request(ablation, results):
    baseline = results["baseline"]
    per_request = baseline.cost_usd / baseline.requests
    assert ablation.per_thousand(baseline) == pytest.approx(per_request * 1000)
    assert ablation.per_month(baseline) == pytest.approx(per_request * 1000 * 30)


def test_the_real_sample_shows_the_cache_and_writes_clean_evidence(sample):
    gateway = CachingGateway()
    base = sample.base_env({**GATEWAY_ENV, "LANGSMITH_TRACING": "true"})
    assert base["MODEL_PROVIDER"] == "gateway"
    assert base["LANGSMITH_TRACING"] == "false"
    passes = sample.run_sample(base, gateway.client())
    results = {name: result for name, _label, result in passes}
    assert [name for name, _label, _result in passes] == [
        "baseline",
        "levers",
        "cache_fill",
        "cache_repeat",
    ]
    assert results["levers"].cached_calls == results["cache_fill"].cached_calls == 0
    repeat = results["cache_repeat"]
    assert repeat.cached_calls == repeat.model_calls > 0
    assert repeat.cost_usd == 0
    assert not sample.problems(results)
    report = sample.render(passes)
    for text in ("test-only-token", "test-account", "EMP-0004", "Jorge"):
        assert text not in report
    assert "cache_repeat" in report


def test_the_real_sample_reports_what_failed(sample):
    gateway = FakeGateway()
    base = sample.base_env(dict(GATEWAY_ENV))
    passes = sample.run_sample(base, gateway.client())
    results = {name: result for name, _label, result in passes}
    assert "la caché no respondió en la segunda pasada" in sample.problems(results)
    down = FakeGateway(status=503)
    env = {**base, **sample.PASSES[0][2], "MODEL_MAX_RETRIES": "0"}
    with sample.ablation.app_client(env, down.client()) as client:
        failed = sample.ablation.measure(client, sample.ablation.judge())
    assert failed.ok == 0
    assert "baseline: 8 respuestas con error" in sample.problems({"baseline": failed})


def test_the_real_sample_needs_the_cloudflare_credentials(sample, capsys):
    assert sample.main(environ={"PATH": "x"}) == 2
    assert "CF_ACCOUNT_ID" in capsys.readouterr().out


def test_the_app_client_uses_the_injected_gateway_client(ablation):
    gateway = FakeGateway()
    env = {**ablation.FAKE_ENV, **GATEWAY_ENV}
    with ablation.app_client(env, gateway.client()) as client:
        response = client.post("/ask", json={"question": "¿Quién es EMP-0004?"})
    assert response.status_code == 200
    assert gateway.requests
