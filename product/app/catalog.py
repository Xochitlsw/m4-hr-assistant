"""Catálogo de modelos: los dos que puede elegir el enrutador, con su precio.

Con ``fake`` hay dos modelos simulados con los nombres de ``MODEL_SMALL`` y
``MODEL_LARGE``; con ``gateway``, dos modelos reales. Siempre van envueltos en
:class:`app.pricing.PricedModel`, para que cada llamada diga su costo.
"""

import httpx

from app.config import ConfigError, Settings
from app.directory import EmployeeDirectory
from app.gateway import GatewayModel
from app.llm import ChatModel, FakeModel
from app.pricing import PriceTable, PricedModel


def build_models(
    settings: Settings,
    directory: EmployeeDirectory,
    http_client: httpx.Client | None = None,
    model: ChatModel | None = None,
) -> dict[str, ChatModel]:
    """Crea el modelo pequeño y el grande, con su precio.

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :param directory: Directorio, del que el modelo simulado toma los departamentos
    :type directory: app.directory.EmployeeDirectory
    :param http_client: Cliente HTTP del modelo real (obligatorio con ``gateway``)
    :type http_client: httpx.Client | None
    :param model: Si se pasa, atiende las dos rutas (lo usan las pruebas)
    :type model: app.llm.ChatModel | None
    :return: ``{nombre: modelo con precio}`` para ``MODEL_SMALL`` y ``MODEL_LARGE``
    :rtype: dict[str, app.llm.ChatModel]
    :raises app.config.ConfigError: Si un modelo no tiene precio o falta el cliente
    """
    config = settings.models
    prices = PriceTable.load(config.prices_path)
    prices.require(config.small, config.large)
    names = (config.small, config.large)
    if model is not None:
        bases = {name: model for name in names}
    elif config.provider == "gateway":
        if http_client is None:
            raise ConfigError("MODEL_PROVIDER=gateway needs an HTTP client")
        bases = {name: GatewayModel(name, config, http_client) for name in names}
    else:
        departments = directory.departments()
        bases = {name: FakeModel(departments) for name in names}
    return {name: PricedModel(base, name, prices) for name, base in bases.items()}
