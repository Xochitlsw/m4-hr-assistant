"""API del HR Assistant, sin estado, armada por ``create_app()``.

``create_app()`` recibe sus dependencias (configuración, modelo y directorio) para
que las pruebas inyecten las suyas. ``uvicorn app.main:app`` usa la aplicación
armada con las variables de entorno del proceso.
"""

import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agent import Agent, AgentResult
from app.config import Settings, load_settings
from app.directory import EmployeeDirectory
from app.llm import ChatModel, build_model

PAGE = Path(__file__).resolve().parent / "static" / "index.html"


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


def create_app(
    settings: Settings | None = None,
    model: ChatModel | None = None,
    directory: EmployeeDirectory | None = None,
) -> FastAPI:
    """Arma la aplicación con sus dependencias.

    :param settings: Configuración; si es ``None``, se lee del entorno
    :type settings: Settings | None
    :param model: Modelo del agente; si es ``None``, el de ``MODEL_PROVIDER``
    :type model: ChatModel | None
    :param directory: Directorio; si es ``None``, se carga de ``EMPLOYEES_PATH``
    :type directory: EmployeeDirectory | None
    :return: La aplicación FastAPI
    :rtype: fastapi.FastAPI
    :raises app.config.ConfigError: Si la configuración o los datos son inválidos
    """
    if settings is None:
        settings = load_settings()
    if directory is None:
        directory = EmployeeDirectory.load(settings.employees_path)
    if model is None:
        model = build_model(settings, directory)
    agent = Agent(model, directory, settings.agent_max_steps)
    ask_request = build_ask_request(settings.max_question_chars)
    page = PAGE.read_text(encoding="utf-8")

    app = FastAPI(
        title="HR Assistant",
        version="1.0.0",
        description="Answers questions about employees from the HR directory.",
    )

    @app.get("/", response_class=HTMLResponse, summary="HR Assistant web page")
    def home() -> str:
        """Sirve la página web del personal de RH."""
        return page

    @app.get("/health", summary="Health check with the process id")
    def health() -> dict:
        """Indica que el proceso responde y cuál copia contestó."""
        return {"status": "ok", "pid": os.getpid()}

    @app.post("/ask", response_model=AgentResult, summary="Ask the HR Assistant")
    def ask(body: ask_request) -> AgentResult:
        """Corre el agente con la pregunta y el historial del cliente.

        La respuesta es el :class:`app.agent.AgentResult`: ``answer``, ``steps``,
        ``tools_used``, ``input_tokens`` y ``output_tokens``.
        """
        history = [turn.model_dump() for turn in body.history]
        return agent.run(body.question, history)

    return app


app = create_app()
