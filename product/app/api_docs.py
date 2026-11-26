"""Contrato público de la API en OpenAPI: descripción, etiquetas y errores (ADR 0019).

Lo que ve quien integra la API en ``/docs`` y en ``doc/openapi.json``: está en inglés,
como el resto de la API. Cada error de ``POST /ask`` documenta su cuerpo (``ErrorBody``)
con un ejemplo, y los ``429`` y ``503`` su ``Retry-After``.
"""

from app.schemas import ErrorBody, ReadyResponse

VERSION = "1.1.0"
DESCRIPTION = """\
HR Assistant: an agent that answers questions about employees from the HR directory,
with two read-only tools (`get_employee`, `search_by_department`).

**Authentication**: `POST /ask` and `GET /metrics` need the header `X-API-Key` with the
key of a client (use **Authorize**). `/`, `/health`, `/ready` and `/docs` need no key.

**Limits**: body up to 64 KiB, question up to 2000 characters, history up to 20
messages; per-client rate limit and daily cost budget; failed keys are limited per
origin. Every response carries `X-Request-ID`, to find the request in logs and traces.

**Privacy**: personal data in questions is redacted before the model; a secret or a
prompt injection is rejected with `400`. Logs never contain the text of questions or
answers.
"""
OPENAPI_TAGS = [
    {"name": "ask", "description": "Questions to the HR agent"},
    {"name": "operations", "description": "Health, readiness and metrics"},
    {"name": "page", "description": "Web page for HR staff"},
]
RETRY_AFTER = {
    "Retry-After": {
        "description": "Seconds to wait before trying again",
        "schema": {"type": "integer"},
    }
}


def error(description: str, detail: str, retry_after: bool = False) -> dict:
    """Respuesta de error documentada con ``ErrorBody`` y un ejemplo.

    :param description: Cuándo ocurre
    :type description: str
    :param detail: Ejemplo de ``detail``
    :type detail: str
    :param retry_after: ``True`` si la respuesta trae ``Retry-After``
    :type retry_after: bool
    :return: Entrada para ``responses`` de FastAPI
    :rtype: dict
    """
    documented = {
        "model": ErrorBody,
        "description": description,
        "content": {"application/json": {"example": {"detail": detail}}},
    }
    if retry_after:
        documented["headers"] = RETRY_AFTER
    return documented


ASK_ERRORS = {
    400: error(
        "The question or the history contain a secret or look like a prompt injection",
        "request looks like a prompt injection; ask about employees",
    ),
    401: error("Missing or wrong `X-API-Key`", "invalid or missing API key"),
    413: error("Body larger than `MAX_BODY_BYTES` (checked before reading it)",
               "request body too large"),
    429: error(
        "Too many questions per minute, too many failed keys from this origin, or the "
        "client's daily budget is spent",
        "rate limit exceeded",
        retry_after=True,
    ),
    502: error("The model failed after its retries", "model unavailable"),
    503: error(
        "The model circuit is open: it failed several times in a row",
        "model temporarily unavailable",
        retry_after=True,
    ),
}
METRICS_RESPONSES = {
    401: error("Missing or wrong `X-API-Key`", "invalid or missing API key"),
    429: error(
        "Too many failed keys from this origin",
        "too many failed authentication attempts",
        retry_after=True,
    ),
}
READY_RESPONSES = {
    503: {
        "model": ReadyResponse,
        "description": "Not ready: every model circuit is open",
    }
}
