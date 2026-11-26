"""Log de eventos: una línea JSON por evento en la salida estándar (ADR 0004).

Cada línea lleva ``ts`` (ISO 8601 en UTC), ``service``, ``event`` y ``request_id``.
Los caracteres no ASCII se escapan (``\\u00ed``): la línea sigue siendo JSON válido y
no falla aunque la salida use una codificación limitada.
"""

import json
import sys
import threading
from typing import Any, TextIO

from app.clock import Clock


class EventLogger:
    """Escribe eventos estructurados; seguro con varios hilos.

    :param service: Valor del campo ``service``
    :type service: str
    :param clock: Reloj para el campo ``ts``
    :type clock: app.clock.Clock
    :param stream: Destino; si es ``None``, la salida estándar del momento
    :type stream: TextIO | None
    """

    def __init__(self, service: str, clock: Clock, stream: TextIO | None = None):
        self._service = service
        self._clock = clock
        self._stream = stream
        self._lock = threading.Lock()

    def emit(self, event: str, request_id: str, **fields: Any) -> None:
        """Escribe un evento como una línea JSON.

        Los campos comunes van primero y no se pueden reemplazar con ``fields``.

        :param event: Nombre del evento: ``request``, ``agent_run``, ``audit`` o
            ``error``
        :type event: str
        :param request_id: Id de la petición
        :type request_id: str
        :param fields: Campos propios del evento; nunca texto de preguntas o respuestas
        :type fields: Any
        """
        record = {
            "ts": self._clock.now().isoformat(),
            "service": self._service,
            "event": event,
            "request_id": request_id,
        }
        for key, value in fields.items():
            record.setdefault(key, value)
        line = json.dumps(record, default=str) + "\n"
        stream = self._stream or sys.stdout
        with self._lock:
            stream.write(line)
            stream.flush()
