"""Catálogo de modelos: los dos que puede elegir el enrutador, con su precio.

Con ``fake`` hay dos modelos simulados con los nombres de ``MODEL_SMALL`` y
``MODEL_LARGE``; con ``gateway``, dos modelos reales. Siempre van envueltos en
:class:`app.pricing.PricedModel`, para que cada llamada diga su costo; los reales,
además, en :class:`app.resilience.ResilientModel` (reintentos y circuit breaker, cada
uno con su propio circuito; ADR 0018).
"""

from collections.abc import Callable

import httpx

from app.clock import Clock, SystemClock
from app.config import ConfigError, Settings
from app.directory import EmployeeDirectory
from app.gateway import GatewayModel
from app.llm import ChatModel, FakeModel
from app.pricing import PriceTable, PricedModel
from app.resilience import ResilientModel


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


def add_resilience(
    models: dict[str, ChatModel],
    settings: Settings,
    clock: Clock | None = None,
    on_event: Callable[..., None] | None = None,
) -> dict[str, ChatModel]:
    """Envuelve cada modelo real en reintentos y su propio circuit breaker.

    :param models: Modelos con precio (:func:`build_models`)
    :type models: dict[str, app.llm.ChatModel]
    :param settings: Configuración validada
    :type settings: app.config.Settings
    :param clock: Reloj de los reintentos y del circuito (por omisión, el del sistema)
    :type clock: app.clock.Clock | None
    :param on_event: Aviso de cambios de estado de un circuito
    :type on_event: Callable[..., None] | None
    :return: Los mismos modelos, resilientes
    :rtype: dict[str, app.llm.ChatModel]
    """
    gateway = settings.models.gateway
    return {
        name: ResilientModel(
            priced,
            gateway.resilience,
            clock or SystemClock(),
            timeout_seconds=gateway.timeout_seconds,
            on_event=on_event,
        )
        for name, priced in models.items()
    }
