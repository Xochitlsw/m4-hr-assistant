"""Pruebas del costo por llamada con los precios de ``data/model_prices.json``."""

import pytest

from app.config import ConfigError, load_settings
from app.llm import Completion
from app.main import create_app
from app.pricing import PriceTable, PricedModel
from tests.conftest import LARGE, PRODUCT, SMALL

PRICES = PRODUCT / "data" / "model_prices.json"


class FixedModel:
    """Modelo que siempre devuelve la misma ``Completion``."""

    def __init__(self, completion):
        self.completion = completion

    def complete(self, messages):
        assert messages
        return self.completion


def test_cost_uses_the_price_of_each_model():
    prices = PriceTable.load(PRICES)
    assert prices.cost(SMALL, 1_000_000, 1_000_000) == pytest.approx(0.429)
    assert prices.cost(LARGE, 1_000_000, 1_000_000) == pytest.approx(2.546)
    assert prices.cost(SMALL, 1000, 0) == pytest.approx(0.000045)


def test_a_cached_call_costs_nothing():
    prices = PriceTable.load(PRICES)
    messages = [{"role": "user", "content": "Hola"}]
    fresh = PricedModel(FixedModel(Completion("{}", 1000, 100)), SMALL, prices)
    cached = PricedModel(
        FixedModel(Completion("{}", 1000, 100, cached=True)), SMALL, prices
    )
    assert fresh.complete(messages).cost_usd == pytest.approx(0.0000834)
    assert cached.complete(messages).cost_usd == 0
    assert cached.complete(messages).input_tokens == 1000


def test_a_model_without_a_price_stops_the_start():
    settings = load_settings({"MODEL_SMALL": "@cf/unknown/model"})
    with pytest.raises(ConfigError, match="@cf/unknown/model"):
        create_app(settings=settings)
