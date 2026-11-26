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

        model_config = ConfigDict(extra="forbid")

        question: question_type
        history: list[Message] = Field(
            default_factory=list, max_length=limits.history_messages
        )

    return AskRequest


class AskResponse(BaseModel):
    """Respuesta de ``POST /ask``."""

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
