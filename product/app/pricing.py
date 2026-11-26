"""Costo por llamada con los precios por modelo (ADR 0008).

Los precios están en USD por millón de tokens (``data/model_prices.json``). Una
llamada que el gateway contestó desde su caché no cuesta.
"""

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from app.config import ConfigError
from app.llm import ChatModel, Completion

TOKENS_PER_PRICE_UNIT = 1_000_000


class PriceTable:
    """Precios de entrada y salida por modelo.

    :param prices: ``{modelo: {"input": precio, "output": precio}}``
    :type prices: Mapping[str, Mapping[str, float]]
    """

    def __init__(self, prices: Mapping[str, Mapping[str, float]]):
        self._prices = {
            name: (float(price["input"]), float(price["output"]))
            for name, price in prices.items()
        }

    @classmethod
    def load(cls, path: Path) -> "PriceTable":
        """Carga los precios de un archivo JSON con la llave ``models``.

        :param path: Ruta del archivo de precios
        :type path: pathlib.Path
        :return: La tabla de precios
        :rtype: PriceTable
        :raises ConfigError: Si el archivo no existe o no tiene el formato esperado
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(data["models"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigError(
                f"PRICES_PATH must be a readable JSON file with prices: {path}"
            ) from error

    def require(self, *models: str) -> None:
        """Comprueba que cada modelo tenga precio.

        :param models: Nombres de modelo
        :type models: str
        :raises ConfigError: Si alguno no tiene precio
        """
        missing = [name for name in models if name not in self._prices]
        if missing:
            raise ConfigError(f"PRICES_PATH has no price for: {', '.join(missing)}")

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Costo en USD de una llamada.

        :param model: Nombre del modelo
        :type model: str
        :param input_tokens: Tokens de entrada
        :type input_tokens: int
        :param output_tokens: Tokens de salida
        :type output_tokens: int
        :return: Costo en USD
        :rtype: float
        """
        input_price, output_price = self._prices[model]
        total = input_tokens * input_price + output_tokens * output_price
        return total / TOKENS_PER_PRICE_UNIT


class PricedModel:
    """Envuelve un modelo y pone ``cost_usd`` en cada ``Completion``.

    :param model: Modelo simulado o real
    :type model: app.llm.ChatModel
    :param name: Nombre del modelo, con el que se busca su precio
    :type name: str
    :param prices: Tabla de precios
    :type prices: PriceTable
    """

    def __init__(self, model: ChatModel, name: str, prices: PriceTable):
        self.name = name
        self._model = model
        self._prices = prices

    def complete(self, messages: list[dict]) -> Completion:
        """Llama al modelo y calcula el costo; una llamada cacheada cuesta 0.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta con su costo
        :rtype: app.llm.Completion
        """
        completion = self._model.complete(messages)
        if completion.cached:
            return replace(completion, cost_usd=0.0)
        cost = self._prices.cost(
            self.name, completion.input_tokens, completion.output_tokens
        )
        return replace(completion, cost_usd=cost)
