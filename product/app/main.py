"""API del HR Assistant, sin estado, armada por ``create_app()``.

``create_app()`` recibe sus dependencias (configuración, modelo, directorio,
observabilidad y cliente HTTP) para que las pruebas inyecten las suyas.
``uvicorn app.main:app`` usa la aplicación armada con las variables de entorno del
proceso. Antes de armar nada aplica las reglas de producción (``app.compliance``).
"""

import os
import re
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader

from app import compliance
from app.agent import Agent
from app.catalog import build_models
from app.clock import Clock, SystemClock
from app.config import Settings, load_settings
from app.directory import EmployeeDirectory
from app.gateway import ModelUnavailable, build_http_client
from app.llm import ChatModel
from app.logs import EventLogger
from app.metrics import Metrics
from app.routing import Router, apply_window
from app.schemas import AskResponse, build_ask_request
from app.security import KeyStore, RateLimiter
from app.tracing import Tracer

PAGE = Path(__file__).resolve().parent / "static" / "index.html"
REQUEST_ID_HEADER = "X-Request-ID"
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# auto_error=False: el 401 lo arma la aplicación con el texto de la especificación.
API_KEY_SCHEME = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="API key of the client"
)


@dataclass(frozen=True)
class Observability:
    """Piezas de observabilidad que comparte la aplicación.

    :param clock: Reloj para el log, las latencias y el límite de peticiones
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


class AskService:
    """Atiende ``POST /ask``: enruta, recorta el historial, corre el agente y registra.

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :param models: ``{nombre: modelo con precio}`` para el modelo pequeño y el grande
    :type models: Mapping[str, app.llm.ChatModel]
    :param directory: Directorio de empleados
    :type directory: app.directory.EmployeeDirectory
    :param observability: Log, métricas y trazas
    :type observability: Observability
    """

    def __init__(
        self,
        settings: Settings,
        models: Mapping[str, ChatModel],
        directory: EmployeeDirectory,
        observability: Observability,
    ):
        self._router = Router(
            settings.routing, settings.models.small, settings.models.large
        )
        self._window = settings.routing.history_window
        self._models = dict(models)
        self._directory = directory
        self._max_steps = settings.agent_max_steps
        self._observability = observability

    def answer(
        self, question: str, history: list[dict], request_id: str, client: str
    ) -> AskResponse:
        """Responde una pregunta y deja su rastro sin guardar el texto ni la clave.

        :param question: Pregunta ya validada
        :type question: str
        :param history: Historial completo que mandó el cliente
        :type history: list[dict]
        :param request_id: Id de la petición
        :type request_id: str
        :param client: Cliente autenticado
        :type client: str
        :return: La respuesta de la API, con modelo, ruta y costo
        :rtype: app.schemas.AskResponse
        :raises app.gateway.ModelUnavailable: Si el modelo real falla
        """
        decision = self._router.choose(question)
        sent = apply_window(history, self._window)
        log = self._observability.logger

        def audit(tool: str, args: dict) -> None:
            log.emit(
                "audit",
                request_id,
                action="tool_call",
                client=client,
                tool=tool,
                args=args,
            )

        agent = Agent(self._models[decision.model], self._directory, self._max_steps)
        metadata = {"client": client, "model": decision.model, "route": decision.route}
        with self._observability.tracer.request(request_id, **metadata):
            result = agent.run(question, sent, on_tool_call=audit)
        response = AskResponse.from_result(result, request_id, decision)
        log.emit(
            "agent_run",
            request_id,
            client=client,
            question_chars=len(question),
            history_messages=len(history),
            history_sent=len(sent),
            steps=result.steps,
            tools=result.tools_used,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            stopped=result.stopped,
            model=decision.model,
            route=decision.route,
            cached_calls=response.cached_calls,
            cost_usd=response.cost_usd,
        )
        metrics = self._observability.metrics
        metrics.add_tokens(result.input_tokens, result.output_tokens)
        metrics.add_cost(result.usage.cost_usd)
        return response


def _lifespan(client: httpx.Client | None) -> Callable:
    """Ciclo de vida: al apagar cierra el cliente HTTP, si la aplicación lo creó."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if client is not None:
            client.close()

    return lifespan


def _access_dependencies(
    settings: Settings, observability: Observability
) -> tuple[Callable, Callable]:
    """Dependencias de FastAPI: clave por cliente (401) y límite de peticiones (429).

    Corren antes de validar los campos del cuerpo. Auditan los rechazos sin escribir
    la clave presentada.

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :param observability: Reloj y log
    :type observability: Observability
    :return: ``(require_client, limit_rate)``
    :rtype: tuple[Callable, Callable]
    """
    keys = KeyStore(settings.security.api_keys)
    limiter = RateLimiter(settings.security.rate_limit_per_minute, observability.clock)
    log = observability.logger

    def require_client(
        request: Request, key: str | None = Security(API_KEY_SCHEME)
    ) -> str:
        """Devuelve el cliente de la clave, o responde 401."""
        client = keys.client_for(key)
        if client is None:
            log.emit(
                "audit",
                request.state.request_id,
                action="auth_failed",
                reason="invalid" if key else "missing",
                path=request.url.path,
            )
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        request.state.client = client
        return client

    def limit_rate(request: Request, client: str = Depends(require_client)) -> str:
        """Deja pasar al cliente si no superó su límite; si no, responde 429."""
        retry_after = limiter.hit(client)
        if retry_after is not None:
            log.emit(
                "audit",
                request.state.request_id,
                action="rate_limited",
                client=client,
                retry_after=retry_after,
            )
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        return client

    return require_client, limit_rate


def _add_observability(app: FastAPI, observability: Observability) -> None:
    """Middleware de petición y respuesta ``502`` cuando el modelo real falla."""
    log = observability.logger

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
            client=getattr(request.state, "client", None),
            method=request.method,
            path=path,
            status=response.status_code,
            latency_ms=latency_ms,
        )
        return response

    @app.exception_handler(ModelUnavailable)
    async def model_unavailable(request: Request, error: ModelUnavailable):
        """El modelo real no respondió: ``502`` con el ``request_id``."""
        request_id = request.state.request_id
        log.emit(
            "error",
            request_id,
            client=getattr(request.state, "client", None),
            error_type=type(error).__name__,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "model unavailable", "request_id": request_id},
        )


def _add_routes(
    app: FastAPI,
    settings: Settings,
    service: AskService,
    metrics: Metrics,
    access: tuple[Callable, Callable],
) -> None:
    """Rutas de la API: página y salud sin clave; métricas y preguntas con clave."""
    require_client, limit_rate = access
    ask_request = build_ask_request(settings.max_question_chars)
    page = PAGE.read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse, summary="HR Assistant web page")
    def home() -> str:
        """Sirve la página web del personal de RH."""
        return page

    @app.get("/health", summary="Health check with the process id")
    def health() -> dict:
        """Indica que el proceso responde y cuál copia contestó."""
        return {"status": "ok", "pid": os.getpid()}

    @app.get(
        "/metrics",
        summary="Latency, errors, tokens, cost and SLO",
        dependencies=[Depends(require_client)],
    )
    def process_metrics() -> dict:
        """Métricas del tráfico de usuarios de este proceso."""
        return metrics.snapshot()

    @app.post("/ask", response_model=AskResponse, summary="Ask the HR Assistant")
    def ask(
        body: ask_request, request: Request, client: str = Depends(limit_rate)
    ) -> AskResponse:
        """Enruta la pregunta, corre el agente y responde con modelo, ruta y costo."""
        history = [turn.model_dump() for turn in body.history]
        return service.answer(
            body.question, history, request.state.request_id, client
        )


def create_app(
    settings: Settings | None = None,
    model: ChatModel | None = None,
    directory: EmployeeDirectory | None = None,
    observability: Observability | None = None,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    """Arma la aplicación con sus dependencias.

    Primero aplica las reglas de producción. El cliente HTTP del modelo real solo se
    crea con ``MODEL_PROVIDER=gateway`` (y se cierra al apagar).

    :param settings: Configuración; si es ``None``, se lee del entorno
    :type settings: Settings | None
    :param model: Si se pasa, atiende las dos rutas del enrutador (pruebas)
    :type model: ChatModel | None
    :param directory: Directorio; si es ``None``, se carga de ``EMPLOYEES_PATH``
    :type directory: EmployeeDirectory | None
    :param observability: Reloj, log, métricas y trazas; si es ``None``, los reales
    :type observability: Observability | None
    :param http_client: Cliente HTTP del modelo real; si es ``None`` y hace falta, se
        crea uno
    :type http_client: httpx.Client | None
    :return: La aplicación FastAPI
    :rtype: fastapi.FastAPI
    :raises app.config.ConfigError: Si la configuración, los datos o los precios son
        inválidos, o si producción no cumple sus reglas
    """
    if settings is None:
        settings = load_settings()
    compliance.enforce(settings)
    if directory is None:
        directory = EmployeeDirectory.load(settings.employees_path)
    if observability is None:
        observability = Observability.from_settings(settings)
    owned_client = None
    if http_client is None and model is None and settings.models.provider == "gateway":
        owned_client = http_client = build_http_client(settings.models)
    models = build_models(settings, directory, http_client, model)
    service = AskService(settings, models, directory, observability)
    app = FastAPI(
        title="HR Assistant",
        version="1.0.0",
        description="Answers questions about employees from the HR directory.",
        lifespan=_lifespan(owned_client),
    )
    _add_observability(app, observability)
    access = _access_dependencies(settings, observability)
    _add_routes(app, settings, service, observability.metrics, access)
    return app


app = create_app()
