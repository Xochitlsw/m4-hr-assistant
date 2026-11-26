"""Trazas opcionales en LangSmith (ADR 0006 y 0014).

Las trazas se encienden solo con ``LANGSMITH_TRACING=true`` y una clave presente. El
cliente se crea según ``HIDE_INPUTS``/``HIDE_OUTPUTS``: ``true`` oculta esa dirección,
``redact`` la manda redactada por ``app.privacy`` (como ``anonymizer``) y ``false`` la
manda en claro (producción no lo acepta). El cliente se pasa en el contexto de cada
petición, para que ninguna traza use otro. El producto nunca lee el valor de la clave:
lo toma el cliente de LangSmith del entorno.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langsmith import Client, get_current_run_tree, traceable, tracing_context

from app.config import Settings
from app.llm import Completion
from app.privacy import redact_tree


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
        options = {
            "hide_inputs": observability.hide_inputs,
            "hide_outputs": observability.hide_outputs,
        }
        if observability.redacts:
            # LangSmith aplica el anonymizer al JSON ya serializado de entradas,
            # salidas, metadatos y errores; lo oculto sigue oculto (ADR 0014).
            options["anonymizer"] = redact_tree
        return cls(client_factory(**options))

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
    run_type: str, name: str, function: Callable[..., Any], **options: Any
) -> Callable[..., Any]:
    """Envuelve una función para que quede como traza si el contexto lo permite.

    :param run_type: Tipo de corrida de LangSmith (``chain``, ``llm`` o ``tool``)
    :type run_type: str
    :param name: Nombre de la traza
    :type name: str
    :param function: Función a envolver
    :type function: Callable[..., Any]
    :param options: ``metadata``, ``tags`` o ``process_outputs`` de ``traceable``
    :type options: Any
    :return: La función envuelta; sin trazas encendidas se comporta igual
    :rtype: Callable[..., Any]
    """
    return traceable(run_type=run_type, name=name, **options)(function)


def annotate(**metadata: Any) -> None:
    """Agrega metadatos a la traza en curso; sin traza, no hace nada.

    Sirve para lo que se sabe al final (modelo, ruta, costo), que no estaba al abrir la
    traza.

    :param metadata: Metadatos a agregar
    :type metadata: Any
    """
    run = get_current_run_tree()
    if run is not None:
        run.add_metadata(metadata)


def completion_outputs(completion: Completion) -> dict:
    """Salida de una llamada al modelo para LangSmith, con ``usage_metadata``.

    LangSmith toma ``usage_metadata`` para mostrar tokens y costo de la llamada.

    :param completion: Respuesta del modelo
    :type completion: app.llm.Completion
    :return: Texto, uso de tokens, caché, costo e intentos
    :rtype: dict
    """
    return {
        "text": completion.text,
        "usage_metadata": {
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "total_tokens": completion.input_tokens + completion.output_tokens,
        },
        "cached": completion.cached,
        "cost_usd": completion.cost_usd,
        "attempts": completion.attempts,
    }
