"""API del HR Assistant, sin estado, armada por ``create_app()``.

``create_app()`` recibe sus dependencias (configuración, modelo, directorio y
observabilidad) para que las pruebas inyecten las suyas. ``uvicorn app.main:app``
usa la aplicación armada con las variables de entorno del proceso.
"""

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.agent import Agent
from app.clock import Clock, SystemClock
from app.config import Settings, load_settings
from app.directory import EmployeeDirectory
from app.llm import ChatModel, build_model
from app.logs import EventLogger
from app.metrics import Metrics
from app.schemas import AskResponse, build_ask_request
from app.tracing import Tracer

PAGE = Path(__file__).resolve().parent / "static" / "index.html"
REQUEST_ID_HEADER = "X-Request-ID"
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


@dataclass(frozen=True)
class Observability:
    """Piezas de observabilidad que comparte la aplicación.

    :param clock: Reloj para el log y las latencias
    :type clock: app.clock.Clock
    :param logger: Log de eventos JSON
    :type logger: app.logs.EventLogger
    :param metrics: Métricas de este proceso
    :type metrics: app.metrics.Metrics
    :param tracer: Contexto de trazas de LangSmith
    :type tracer: app.tracing.Tracer
    """

    clock: Clock
    logger: EventLogger
    metrics: Metrics
    tracer: Tracer

    @classmethod
    def from_settings(cls, settings: Settings) -> "Observability":
        """Arma las piezas reales a partir de la configuración.

        :param settings: Configuración validada
        :type settings: app.config.Settings
        :return: Reloj del sistema, log a ``stdout``, métricas y trazas
        :rtype: Observability
        """
        clock = SystemClock()
        config = settings.observability
        return cls(
            clock=clock,
            logger=EventLogger(config.service_name, clock),
            metrics=Metrics(config.slo_p95_ms),
            tracer=Tracer.from_settings(settings),
        )


def resolve_request_id(sent: str | None) -> str:
    """Usa el ``X-Request-ID`` del cliente si es seguro; si no, genera un UUID.

    :param sent: Valor del encabezado, o ``None`` si no vino
    :type sent: str | None
    :return: El id de la petición
    :rtype: str
    """
    if sent and SAFE_REQUEST_ID.fullmatch(sent):
        return sent
    return str(uuid.uuid4())


def create_app(
    settings: Settings | None = None,
    model: ChatModel | None = None,
    directory: EmployeeDirectory | None = None,
    observability: Observability | None = None,
) -> FastAPI:
    """Arma la aplicación con sus dependencias.

    :param settings: Configuración; si es ``None``, se lee del entorno
    :type settings: Settings | None
    :param model: Modelo del agente; si es ``None``, el de ``MODEL_PROVIDER``
    :type model: ChatModel | None
    :param directory: Directorio; si es ``None``, se carga de ``EMPLOYEES_PATH``
    :type directory: EmployeeDirectory | None
    :param observability: Reloj, log, métricas y trazas; si es ``None``, los reales
    :type observability: Observability | None
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
    if observability is None:
        observability = Observability.from_settings(settings)
    agent = Agent(model, directory, settings.agent_max_steps)
    ask_request = build_ask_request(settings.max_question_chars)
    page = PAGE.read_text(encoding="utf-8")
    log = observability.logger

    app = FastAPI(
        title="HR Assistant",
        version="1.0.0",
        description="Answers questions about employees from the HR directory.",
    )

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        """Fija el ``request_id``, mide la petición y convierte fallas en 500."""
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        start = observability.clock.monotonic()
        try:
            response = await call_next(request)
        # Frontera de la API: cualquier falla se vuelve un 500 sin detalles internos.
        except Exception as error:  # pylint: disable=broad-exception-caught
            log.emit("error", request_id, error_type=type(error).__name__)
            response = JSONResponse(
                status_code=500,
                content={"detail": "internal error", "request_id": request_id},
            )
        latency_ms = round((observability.clock.monotonic() - start) * 1000, 3)
        response.headers[REQUEST_ID_HEADER] = request_id
        path = request.url.path
        observability.metrics.record_request(path, response.status_code, latency_ms)
        log.emit(
            "request",
            request_id,
            method=request.method,
            path=path,
            status=response.status_code,
            latency_ms=latency_ms,
        )
        return response

    @app.get("/", response_class=HTMLResponse, summary="HR Assistant web page")
    def home() -> str:
        """Sirve la página web del personal de RH."""
        return page

    @app.get("/health", summary="Health check with the process id")
    def health() -> dict:
        """Indica que el proceso responde y cuál copia contestó."""
        return {"status": "ok", "pid": os.getpid()}

    @app.get("/metrics", summary="Latency, errors, tokens and SLO of this process")
    def metrics() -> dict:
        """Métricas del tráfico de usuarios de este proceso."""
        return observability.metrics.snapshot()

    @app.post("/ask", response_model=AskResponse, summary="Ask the HR Assistant")
    def ask(body: ask_request, request: Request) -> AskResponse:
        """Corre el agente, audita sus herramientas y registra la corrida sin texto."""
        request_id = request.state.request_id
        history = [turn.model_dump() for turn in body.history]

        def audit(tool: str, args: dict) -> None:
            log.emit("audit", request_id, action="tool_call", tool=tool, args=args)

        with observability.tracer.request(request_id):
            result = agent.run(body.question, history, on_tool_call=audit)
        log.emit(
            "agent_run",
            request_id,
            question_chars=len(body.question),
            history_messages=len(history),
            steps=result.steps,
            tools=result.tools_used,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            stopped=result.stopped,
        )
        observability.metrics.add_tokens(result.input_tokens, result.output_tokens)
        return AskResponse.from_result(result, request_id)

    return app


app = create_app()
