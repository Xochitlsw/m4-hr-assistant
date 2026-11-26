"""Tope del cuerpo de las peticiones: ``413`` antes de leer de más (ADR 0013).

Un cuerpo enorme ocupa memoria y CPU aunque después falle la validación o la clave.
Por eso el tope corre antes que todo lo demás de la aplicación:

- Si ``Content-Length`` declara más de ``max_bytes``, responde ``413`` sin leer nada.
- Si el cuerpo llega por partes (sin ``Content-Length``), cuenta los bytes mientras
  llegan y corta con ``413`` en cuanto pasan del tope.

El middleware de observabilidad lo envuelve: el ``413`` lleva ``X-Request-ID`` y
queda en el log como cualquier respuesta.
"""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

STATUS = 413
DETAIL = "request body too large"


class BodyTooLarge(HTTPException):
    """El cuerpo pasó del tope mientras llegaba; FastAPI lo responde como ``413``."""

    def __init__(self) -> None:
        super().__init__(status_code=STATUS, detail=DETAIL)


def declared_length(scope: Scope) -> int | None:
    """Largo que declara ``Content-Length``, o ``None`` si falta o no es un entero.

    :param scope: Scope ASGI de la petición
    :type scope: dict
    :return: Bytes declarados
    :rtype: int | None
    """
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodySizeLimit:
    """Middleware ASGI que rechaza los cuerpos de más de ``max_bytes``.

    :param app: Aplicación ASGI que envuelve
    :type app: starlette.types.ASGIApp
    :param max_bytes: Tamaño máximo del cuerpo en bytes
    :type max_bytes: int
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Deja pasar la petición, o responde ``413`` si el cuerpo pasa del tope."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            response = JSONResponse({"detail": DETAIL}, status_code=STATUS)
            await response(scope, receive, send)
            return
        received = 0

        async def counted_receive() -> Message:
            """Entrega cada parte del cuerpo y corta si la suma pasa del tope."""
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge()
            return message

        await self.app(scope, counted_receive, send)
