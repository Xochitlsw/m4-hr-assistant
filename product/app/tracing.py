"""Trazas opcionales en LangSmith (ADR 0006).

Las trazas se encienden solo con ``LANGSMITH_TRACING=true`` y una clave presente. El
cliente se crea con ``hide_inputs``/``hide_outputs`` y se pasa en el contexto de cada
petición, para que ninguna traza use otro cliente. El producto nunca lee el valor de
la clave: lo toma el cliente de LangSmith del entorno.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langsmith import Client, traceable, tracing_context

from app.config import Settings


class Tracer:
    """Contexto de trazas de cada petición; apagado si no hay cliente.

    :param client: Cliente de LangSmith ya configurado, o ``None`` para no trazar
    :type client: langsmith.Client | None
    """

    def __init__(self, client: Client | None = None):
        self.client = client

    @property
    def enabled(self) -> bool:
        """Indica si las trazas están encendidas.

        :return: ``True`` si hay cliente
        :rtype: bool
        """
        return self.client is not None

    @classmethod
    def from_settings(
        cls, settings: Settings, client_factory: Callable[..., Client] = Client
    ) -> "Tracer":
        """Crea el trazador según la configuración.

        :param settings: Configuración validada
        :type settings: app.config.Settings
        :param client_factory: Constructor del cliente (las pruebas pasan uno sin red)
        :type client_factory: Callable[..., langsmith.Client]
        :return: Un trazador encendido o apagado
        :rtype: Tracer
        """
        observability = settings.observability
        if not observability.tracing_enabled:
            return cls()
        return cls(
            client_factory(
                hide_inputs=observability.hide_inputs,
                hide_outputs=observability.hide_outputs,
            )
        )

    @contextmanager
    def request(self, request_id: str, **metadata: str) -> Iterator[None]:
        """Contexto de una petición: este cliente y sus metadatos.

        :param request_id: Id de la petición
        :type request_id: str
        :param metadata: Metadatos extra (por ejemplo ``model`` y ``route``)
        :type metadata: str
        """
        with tracing_context(
            enabled=self.enabled,
            client=self.client,
            metadata={"request_id": request_id, **metadata},
        ):
            yield


def traced(
    run_type: str, name: str, function: Callable[..., Any]
) -> Callable[..., Any]:
    """Envuelve una función para que quede como traza si el contexto lo permite.

    :param run_type: Tipo de corrida de LangSmith (``chain``, ``llm`` o ``tool``)
    :type run_type: str
    :param name: Nombre de la traza
    :type name: str
    :param function: Función a envolver
    :type function: Callable[..., Any]
    :return: La función envuelta; sin trazas encendidas se comporta igual
    :rtype: Callable[..., Any]
    """
    return traceable(run_type=run_type, name=name)(function)
