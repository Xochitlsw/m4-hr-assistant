"""Modelos Pydantic de la API: cuerpo de ``POST /ask`` y su respuesta.

El cuerpo se valida con los límites de la configuración (ADR 0013): largo de la
pregunta, cantidad de mensajes del historial, largo de cada mensaje según su rol y
ningún carácter prohibido (``app.sanitize``). Cualquier falla responde ``422``.
"""

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.agent import AgentResult
from app.config import Settings
from app.routing import RouteDecision
from app.sanitize import has_forbidden_chars

COST_DECIMALS = 8
ASK_EXAMPLE = {
    "question": "¿Quiénes trabajan en Finanzas?",
    "history": [
        {"role": "user", "content": "¿Quién es EMP-0004?"},
        {"role": "assistant", "content": "Jorge Díaz, Desarrollador en Tecnología."},
    ],
}


class ErrorBody(BaseModel):
    """Cuerpo de un error de la API."""

    detail: str = Field(description="What went wrong, in English")
    request_id: str | None = Field(
        default=None, description="Id of the request (on 500, 502 and 503)"
    )


class HealthResponse(BaseModel):
    """Respuesta de ``GET /health``."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "ok", "pid": 7}]}
    )

    status: Literal["ok"] = Field(description="The process answers")
    pid: int = Field(description="Process that answered (state is per process)")


class ReadyChecks(BaseModel):
    """Comprobaciones de ``GET /ready``."""

    directory: str = Field(description="`ok` if the employee directory is loaded")
    prices: str = Field(description="`ok` if every model has a price")
    models: dict[str, str] = Field(
        description="Circuit of each model: `closed`, `open` or `half_open`"
    )


class ReadyResponse(BaseModel):
    """Respuesta de ``GET /ready``."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ready",
                    "checks": {
                        "directory": "ok",
                        "prices": "ok",
                        "models": {"@cf/meta/llama-3.1-8b-instruct-fast": "closed"},
                    },
                }
            ]
        }
    )

    status: Literal["ready", "not_ready"] = Field(
        description="`ready` if the data is loaded and a model circuit is not open"
    )
    checks: ReadyChecks


def reject_forbidden_chars(text: str) -> str:
    """Validador: el texto no trae caracteres de control ni marcas bidireccionales.

    :param text: Pregunta o contenido de un mensaje
    :type text: str
    :return: El mismo texto
    :rtype: str
    :raises ValueError: Si trae alguno (Pydantic lo convierte en ``422``)
    """
    if has_forbidden_chars(text):
        raise ValueError("text contains control or bidirectional characters")
    return text


def build_ask_request(settings: Settings) -> type[BaseModel]:
    """Crea el modelo del cuerpo de ``POST /ask`` con los límites configurados.

    - ``question``: sin espacios al inicio ni al final, de 1 a ``question_chars``.
    - ``history``: hasta ``history_messages`` mensajes. Cada mensaje ``user`` mide
      como mucho ``question_chars`` y cada ``assistant``, ``answer_chars``: nada más
      largo pudo salir de una conversación real con esta API.

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :return: Clase Pydantic del cuerpo
    :rtype: type[pydantic.BaseModel]
    """
    limits = settings.limits
    role_limits = {"user": limits.question_chars, "assistant": limits.answer_chars}
    question_type = Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=limits.question_chars
        ),
        AfterValidator(reject_forbidden_chars),
    ]

    class Message(BaseModel):
        """Un turno del historial que manda el cliente."""

        model_config = ConfigDict(extra="forbid")

        role: Literal["user", "assistant"]
        content: Annotated[str, AfterValidator(reject_forbidden_chars)]

        @model_validator(mode="after")
        def fits_its_role(self) -> "Message":
            """El contenido no pasa del largo máximo de su rol."""
            limit = role_limits[self.role]
            if len(self.content) > limit:
                raise ValueError(
                    f"{self.role} messages must have at most {limit} characters"
                )
            return self

    class AskRequest(BaseModel):
        """Cuerpo de ``POST /ask``: la pregunta y el historial opcional."""

        model_config = ConfigDict(
            extra="forbid", json_schema_extra={"examples": [ASK_EXAMPLE]}
        )

        question: question_type = Field(description="Question for the HR assistant")
        history: list[Message] = Field(
            default_factory=list,
            max_length=limits.history_messages,
            description="Previous turns of the conversation, oldest first",
        )

    return AskRequest


class AskResponse(BaseModel):
    """Respuesta de ``POST /ask``."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "answer": "Jorge Díaz es Desarrollador en Tecnología, activo.",
                    "steps": 2,
                    "tools_used": ["get_employee"],
                    "input_tokens": 127,
                    "output_tokens": 23,
                    "request_id": "3f0c8f3e-6b1a-4f7e-9d2b-7c1e2a9b5d10",
                    "model": "@cf/meta/llama-3.1-8b-instruct-fast",
                    "route": "simple",
                    "cached_calls": 0,
                    "cost_usd": 0.00001455,
                }
            ]
        }
    )

    answer: str = Field(description="Answer for the HR staff, in Spanish")
    steps: int = Field(description="Model calls used by the agent")
    tools_used: list[str] = Field(description="Tools executed, in order")
    input_tokens: int = Field(description="Input tokens of all model calls")
    output_tokens: int = Field(description="Output tokens of all model calls")
    request_id: str = Field(description="Id to trace this request in logs and traces")
    model: str = Field(description="Model chosen by the router")
    route: str = Field(description="Why the router chose that model")
    cached_calls: int = Field(description="Model calls answered by the gateway cache")
    cost_usd: float = Field(description="Cost of this answer in USD")

    @classmethod
    def from_result(
        cls, result: AgentResult, request_id: str, decision: RouteDecision
    ) -> "AskResponse":
        """Arma la respuesta pública desde el resultado del agente.

        :param result: Resultado del agente (``stopped`` no se expone)
        :type result: app.agent.AgentResult
        :param request_id: Id de la petición
        :type request_id: str
        :param decision: Modelo y motivo que eligió el enrutador
        :type decision: app.routing.RouteDecision
        :return: La respuesta de la API
        :rtype: AskResponse
        """
        usage = result.usage
        return cls(
            answer=result.answer,
            steps=result.steps,
            tools_used=result.tools_used,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            request_id=request_id,
            model=decision.model,
            route=decision.route,
            cached_calls=usage.cached_calls,
            cost_usd=round(usage.cost_usd, COST_DECIMALS),
        )
