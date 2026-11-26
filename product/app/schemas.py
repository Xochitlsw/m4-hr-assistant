"""Modelos Pydantic de la API: cuerpo de ``POST /ask`` y su respuesta."""

from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agent import AgentResult


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

    @classmethod
    def from_result(cls, result: AgentResult, request_id: str) -> "AskResponse":
        """Arma la respuesta pública desde el resultado del agente.

        :param result: Resultado del agente (``stopped`` no se expone)
        :type result: app.agent.AgentResult
        :param request_id: Id de la petición
        :type request_id: str
        :return: La respuesta de la API
        :rtype: AskResponse
        """
        data = asdict(result)
        data.pop("stopped")
        return cls(request_id=request_id, **data)
