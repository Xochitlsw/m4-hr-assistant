"""Log de eventos: una línea JSON por evento en la salida estándar (ADR 0004 y 0021).

Cada línea lleva ``ts`` (ISO 8601 en UTC), ``service``, ``event`` y ``request_id``, y
después ``level``, ``environment`` y ``version``, para filtrar en la plataforma de logs.
Los caracteres no ASCII se escapan (``\\u00ed``): la línea sigue siendo JSON válido y
no falla aunque la salida use una codificación limitada.

Un observador opcional recibe cada registro: así ``/metrics`` cuenta lo mismo que el log
(eventos de seguridad, respuestas por modelo y ruta) sin repetir esa lógica.
"""

import json
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from app.clock import Clock

# Acciones de auditoría que son rechazos o señales de abuso: nivel warning.
WARNING_ACTIONS = frozenset(
    {
        "auth_failed",
        "auth_throttled",
        "rate_limited",
        "budget_exceeded",
        "request_rejected",
        "injection_blocked",
        "secret_blocked",
        "output_rejected",
        "startup_refused",
    }
)


def level_for(event: str, fields: dict) -> str:
    """Nivel de un evento: ``error``, ``warning`` o ``info``.

    :param event: Nombre del evento
    :type event: str
    :param fields: Campos del evento
    :type fields: dict
    :return: El nivel
    :rtype: str
    """
    if event == "error":
        return "error"
    if event == "audit" and fields.get("action") in WARNING_ACTIONS:
        return "warning"
    if event == "model_fallback":
        return "warning"
    if event == "model_circuit" and fields.get("state") == "open":
        return "warning"
    return "info"


class EventLogger:
    """Escribe eventos estructurados; seguro con varios hilos.

    :param service: Valor del campo ``service``
    :type service: str
    :param clock: Reloj para el campo ``ts``
    :type clock: app.clock.Clock
    :param stream: Destino; si es ``None``, la salida estándar del momento
    :type stream: TextIO | None
    :param environment: Valor del campo ``environment``
    :type environment: str
    :param version: Versión de la API (campo ``version``)
    :type version: str
    :param observer: Recibe cada registro ya escrito (por ejemplo, las métricas)
    :type observer: Callable[[dict], None] | None
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        service: str,
        clock: Clock,
        stream: TextIO | None = None,
        *,
        environment: str = "development",
        version: str = "",
        observer: Callable[[dict], None] | None = None,
    ):
        self._service = service
        self._clock = clock
        self._stream = stream
        self._context = {"environment": environment, "version": version}
        self._observer = observer
        self._lock = threading.Lock()

    def emit(self, event: str, request_id: str | None, **fields: Any) -> None:
        """Escribe un evento como una línea JSON.

        Los campos comunes van primero y no se pueden reemplazar con ``fields``.

        :param event: Nombre del evento: ``request``, ``agent_run``, ``audit``,
            ``error``, ``model_fallback`` o ``model_circuit``
        :type event: str
        :param request_id: Id de la petición; ``None`` en eventos del proceso
        :type request_id: str | None
        :param fields: Campos propios del evento; nunca texto de preguntas o respuestas
        :type fields: Any
        """
        record = {
            "ts": self._clock.now().isoformat(),
            "service": self._service,
            "event": event,
            "request_id": request_id,
            "level": level_for(event, fields),
            **self._context,
        }
        for key, value in fields.items():
            record.setdefault(key, value)
        line = json.dumps(record, default=str) + "\n"
        stream = self._stream or sys.stdout
        with self._lock:
            stream.write(line)
            stream.flush()
        if self._observer is not None:
            self._observer(record)
