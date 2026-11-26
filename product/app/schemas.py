"""Modelos Pydantic de la API: cuerpo de ``POST /ask`` y su respuesta."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agent import AgentResult
from app.routing import RouteDecision

COST_DECIMALS = 8


class Message(BaseModel):
    """Un turno del historial que manda el cliente."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


def build_ask_request(max_question_chars: int) -> type[BaseModel]:
    """Crea el modelo del cuerpo de ``POST /ask`` con el largo máximo configurado.

    La pregunta se valida sin espacios al inicio ni al final: vacía o más larga que
    ``max_question_chars`` responde ``422``.

    :param max_question_chars: Largo máximo de la pregunta
    :type max_question_chars: int
    :return: Clase Pydantic del cuerpo
    :rtype: type[pydantic.BaseModel]
    """
    question_type = Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=max_question_chars
        ),
    ]

    class AskRequest(BaseModel):
        """Cuerpo de ``POST /ask``: la pregunta y el historial opcional."""

        model_config = ConfigDict(extra="forbid")

        question: question_type
        history: list[Message] = Field(default_factory=list)

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
