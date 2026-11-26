"""API del HR Assistant, sin estado, armada por ``create_app()``.

``create_app()`` recibe sus dependencias (configuración, modelo, directorio,
observabilidad y cliente HTTP) para que las pruebas inyecten las suyas.
``uvicorn app.main:app`` usa la aplicación armada con las variables de entorno del
proceso. Antes de armar nada aplica las reglas de producción (``app.compliance``).
Las entradas se validan antes de llegar al agente (tope del cuerpo y ``app.schemas``)
y la respuesta del modelo se limpia antes de salir (``app.sanitize``; ADR 0013). La
inyección y los secretos bloquean con ``400``, se auditan las acciones relevantes y toda
respuesta lleva cabeceras de seguridad (ADR 0014 y 0016).
"""

import os
import re
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader

from app import compliance
from app.agent import CAP_REACHED, Agent, AgentResult
from app.body_limit import BodySizeLimit
from app.catalog import build_models
from app.clock import Clock, SystemClock
from app.config import ConfigError, Settings, load_settings
from app.directory import EmployeeDirectory
from app.gateway import ModelUnavailable, build_http_client
from app.headers import add_security_headers
from app.injection import InjectionFound, detect_injection, merge_rules
from app.llm import ChatModel
from app.logs import EventLogger
from app.metrics import Metrics
from app.privacy import SecretFound, merge_counts, scan
from app.routing import Router, apply_window
from app.sanitize import BlockedRequest, sanitize_answer
from app.schemas import AskResponse, build_ask_request
from app.security import KeyStore, RateLimiter
from app.tracing import Tracer

PAGE = Path(__file__).resolve().parent / "static" / "index.html"
REQUEST_ID_HEADER = "X-Request-ID"
# Respuestas que se auditan como request_rejected, con su motivo.
REJECTED = {413: "body_too_large", 422: "invalid_input"}
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
        self._max_answer_chars = settings.limits.answer_chars
        self._observability = observability

    def answer(
        self, question: str, history: list[dict], request_id: str, client: str
    ) -> AskResponse:
        """Responde una pregunta y deja su rastro sin guardar el texto ni la clave.

        Antes del modelo, una inyección (``injection_blocked``) o un secreto
        (``secret_blocked``) en la pregunta o el historial bloquean la petición, y los
        datos personales se redactan (``pii_detected``).
        Audita cada herramienta (``tool_call``), cada respuesta del modelo fuera del
        protocolo (``output_rejected``, con su motivo), la limpieza de la respuesta
        (``output_sanitized``, con los cambios) y su redacción (``pii_detected``);
        siempre tipos y cantidades, nunca el texto.

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
        :raises app.sanitize.BlockedRequest: Si la pregunta o el historial traen una
            inyección o un secreto
        """
        question, history = self._screen(question, history, request_id, client)
        decision = self._router.choose(question)
        sent = apply_window(history, self._window)
        on_tool_call, on_invalid_reply = self._auditors(request_id, client)
        agent = Agent(self._models[decision.model], self._directory, self._max_steps)
        metadata = {"client": client, "model": decision.model, "route": decision.route}
        with self._observability.tracer.request(request_id, **metadata):
            result = agent.run(
                question,
                sent,
                on_tool_call=on_tool_call,
                on_invalid_reply=on_invalid_reply,
            )
        result = self._clean(result, request_id, client)
        response = AskResponse.from_result(result, request_id, decision)
        log = self._observability.logger
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

    def _auditors(
        self, request_id: str, client: str
    ) -> tuple[Callable[[str, dict], None], Callable[[str], None]]:
        """Avisos del agente que van a la auditoría, sin texto de la conversación.

        :param request_id: Id de la petición
        :type request_id: str
        :param client: Cliente autenticado
        :type client: str
        :return: ``(on_tool_call, on_invalid_reply)``: ``tool_call`` con la herramienta
            y sus argumentos validados; ``output_rejected`` con el motivo
        :rtype: tuple[Callable, Callable]
        """
        log = self._observability.logger

        def tool_call(tool: str, args: dict) -> None:
            log.emit(
                "audit",
                request_id,
                action="tool_call",
                client=client,
                tool=tool,
                args=args,
            )

        def output_rejected(reason: str) -> None:
            log.emit(
                "audit",
                request_id,
                action="output_rejected",
                client=client,
                reason=reason,
            )

        return tool_call, output_rejected

    def _audit_findings(
        self, action: str, request_id: str, client: str, findings: dict
    ) -> None:
        """Audita, por lugar, lo encontrado; nunca los valores ni el texto.

        :param action: ``pii_detected``, ``secret_blocked`` o ``injection_blocked``
        :type action: str
        :param request_id: Id de la petición
        :type request_id: str
        :param client: Cliente autenticado
        :type client: str
        :param findings: ``{lugar: {tipo: cantidad}}`` o, para la inyección,
            ``{lugar: [reglas]}``; los lugares vacíos no se auditan
        :type findings: dict
        """
        field = "rules" if action == "injection_blocked" else "types"
        for location, found in findings.items():
            if found:
                self._observability.logger.emit(
                    "audit",
                    request_id,
                    action=action,
                    client=client,
                    location=location,
                    **{field: found},
                )

    def _screen(
        self, question: str, history: list[dict], request_id: str, client: str
    ) -> tuple[str, list[dict]]:
        """Revisa la entrada antes del modelo: inyección, secretos y datos personales.

        La inyección (ADR 0016) y los secretos (ADR 0014) bloquean; los datos
        personales se redactan.

        :param question: Pregunta ya validada
        :type question: str
        :param history: Historial que mandó el cliente
        :type history: list[dict]
        :param request_id: Id de la petición
        :type request_id: str
        :param client: Cliente autenticado
        :type client: str
        :return: La pregunta y el historial con los datos personales redactados
        :rtype: tuple[str, list[dict]]
        :raises app.injection.InjectionFound: Si alguno parece una inyección
        :raises app.privacy.SecretFound: Si alguno trae un secreto
        """
        rules = {
            "question": list(detect_injection(question)),
            "history": list(
                merge_rules([detect_injection(turn["content"]) for turn in history])
            ),
        }
        if any(rules.values()):
            self._audit_findings("injection_blocked", request_id, client, rules)
            raise InjectionFound()
        asked = scan(question)
        turns = [scan(turn["content"]) for turn in history]
        secrets = {
            "question": asked.secrets,
            "history": merge_counts(turn.secrets for turn in turns),
        }
        if any(secrets.values()):
            self._audit_findings("secret_blocked", request_id, client, secrets)
            raise SecretFound()
        personal = {
            "question": asked.pii,
            "history": merge_counts(turn.pii for turn in turns),
        }
        self._audit_findings("pii_detected", request_id, client, personal)
        redacted = [
            {"role": turn["role"], "content": found.text}
            for turn, found in zip(history, turns)
        ]
        return asked.text, redacted

    def _clean(self, result: AgentResult, request_id: str, client: str) -> AgentResult:
        """Limpia la respuesta y redacta sus datos personales y secretos.

        Si cambió algo, lo audita: ``output_sanitized`` con los cambios de limpieza y
        ``pii_detected`` con lo redactado (lugar ``answer``).

        :param result: Resultado del agente
        :type result: app.agent.AgentResult
        :param request_id: Id de la petición
        :type request_id: str
        :param client: Cliente autenticado
        :type client: str
        :return: El mismo resultado con la respuesta limpia
        :rtype: app.agent.AgentResult
        """
        clean = sanitize_answer(result.answer, self._max_answer_chars, CAP_REACHED)
        changes = list(clean.changes)
        found = scan(clean.text)
        text = clean.text
        if found.found:
            self._audit_findings(
                "pii_detected", request_id, client, {"answer": found.types}
            )
            # Las marcas [REDACTED:...] pueden alargar el texto: se vuelve a recortar.
            again = sanitize_answer(found.text, self._max_answer_chars, CAP_REACHED)
            text = again.text
            changes += [change for change in again.changes if change not in changes]
        if changes:
            self._observability.logger.emit(
                "audit",
                request_id,
                action="output_sanitized",
                client=client,
                changes=changes,
            )
        return replace(result, answer=text)


def startup_summary(settings: Settings) -> dict:
    """Configuración que se audita al arrancar: nombres y valores, nunca secretos.

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :return: Campos del evento ``startup``
    :rtype: dict
    """
    observability = settings.observability
    return {
        "environment": settings.security.environment,
        "model_provider": settings.models.provider,
        "models": {"small": settings.models.small, "large": settings.models.large},
        "clients": list(settings.security.clients),
        "rate_limit_per_minute": settings.security.rate_limit_per_minute,
        "agent_max_steps": settings.agent_max_steps,
        "limits": asdict(settings.limits),
        "tracing_enabled": observability.tracing_enabled,
        "trace_privacy": {
            "inputs": observability.inputs_privacy,
            "outputs": observability.outputs_privacy,
        },
    }


def _lifespan(client: httpx.Client | None, on_start: Callable[[], None]) -> Callable:
    """Ciclo de vida: al arrancar audita la configuración (``startup``); al apagar
    cierra el cliente HTTP, si la aplicación lo creó."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        on_start()
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
        if response.status_code in REJECTED:
            log.emit(
                "audit",
                request_id,
                action="request_rejected",
                client=getattr(request.state, "client", None),
                reason=REJECTED[response.status_code],
                path=path,
            )
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


def _add_blocked_handler(app: FastAPI) -> None:
    """Una inyección o un secreto en la petición responden ``400`` (ya auditados).

    :param app: La aplicación
    :type app: fastapi.FastAPI
    """

    @app.exception_handler(BlockedRequest)
    async def blocked(_request: Request, error: BlockedRequest):
        """La pregunta o el historial no llegan al modelo; el cuerpo dice por qué."""
        return JSONResponse(status_code=400, content={"detail": error.DETAIL})


def _add_routes(
    app: FastAPI,
    settings: Settings,
    service: AskService,
    metrics: Metrics,
    access: tuple[Callable, Callable],
) -> None:
    """Rutas de la API: página y salud sin clave; métricas y preguntas con clave."""
    require_client, limit_rate = access
    ask_request = build_ask_request(settings)
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
    try:
        compliance.enforce(settings)
    except ConfigError:
        logger = (
            observability.logger
            if observability is not None
            else EventLogger(settings.observability.service_name, SystemClock())
        )
        logger.emit(
            "audit",
            None,
            action="startup_refused",
            environment=settings.security.environment,
            violations=compliance.violations(settings),
        )
        raise
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
        redoc_url=None,
        lifespan=_lifespan(
            owned_client,
            lambda: observability.logger.emit(
                "audit", None, action="startup", **startup_summary(settings)
            ),
        ),
    )
    # Primero el tope del cuerpo: el middleware de observabilidad, que se agrega
    # después, queda por fuera y le pone X-Request-ID y log también al 413.
    app.add_middleware(BodySizeLimit, max_bytes=settings.limits.body_bytes)
    _add_observability(app, observability)
    _add_blocked_handler(app)
    access = _access_dependencies(settings, observability)
    _add_routes(app, settings, service, observability.metrics, access)
    # Al final, para quedar por fuera de todo: también los errores llevan cabeceras.
    production = settings.security.environment == "production"
    add_security_headers(app, PAGE.read_text(encoding="utf-8"), production)
    return app


app = create_app()
