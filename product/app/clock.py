"""Reloj inyectable: la hora del log y el tiempo para medir latencias.

Es el único lugar del producto que lee la hora del sistema. El resto recibe un
:class:`Clock`, y las pruebas inyectan uno fijo para ser deterministas.
"""

import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Interfaz del reloj que usan el log y el middleware de petición."""

    def now(self) -> datetime:
        """Fecha y hora actual, con zona UTC.

        :return: Fecha y hora con ``tzinfo``
        :rtype: datetime.datetime
        """

    def monotonic(self) -> float:
        """Segundos de un reloj que nunca retrocede, para medir duraciones.

        :return: Segundos desde un origen arbitrario
        :rtype: float
        """


class SystemClock:
    """Reloj real del sistema."""

    def now(self) -> datetime:
        """Fecha y hora actual en UTC.

        :return: Fecha y hora con zona UTC
        :rtype: datetime.datetime
        """
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        """Tiempo monótono de alta resolución.

        :return: Segundos desde un origen arbitrario
        :rtype: float
        """
        return time.perf_counter()
