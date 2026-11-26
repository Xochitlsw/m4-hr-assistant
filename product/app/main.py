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
from app import api_docs
from app.agent import CAP_REACHED, Agent, AgentResult
from app.body_limit import BodySizeLimit
from app.catalog import add_resilience, build_models
from app.clock import Clock, SystemClock
from app.config import ConfigError, Settings, load_settings
from app.directory import EmployeeDirectory
from app.gateway import CURRENT_REQUEST_ID, ModelUnavailable, build_http_client
from app.headers import add_security_headers
from app.injection import InjectionFound, detect_injection, merge_rules
from app.llm import ChatModel
from app.logs import EventLogger
from app.metrics import Metrics
from app.privacy import SecretFound, merge_counts, scan
from app.resilience import ModelCircuitOpen
from app.routing import Router, apply_window
from app.sanitize import BlockedRequest, sanitize_answer
from app.schemas import AskResponse, HealthResponse, ReadyResponse, build_ask_request
from app.security import (
    DailyBudget,
    FailureLimiter,
    KeyStore,
    OriginFingerprint,
    RateLimiter,
    client_origin,
)
from app.tracing import Tracer, annotate, traced

PAGE = Path(__file__).resolve().parent / "static" / "index.html"
DASHBOARD = Path(__file__).resolve().parent / "static" / "dashboard.html"
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
        metrics = Metrics(config.slo_p95_ms)
        logger = EventLogger(
            config.service_name,
            clock,
            environment=settings.security.environment,
            version=api_docs.VERSION,
            observer=metrics.observe,
        )
        return cls(
            clock=clock,
            logger=logger,
            metrics=metrics,
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
        self._settings = settings
        self._models = dict(models)
        self._directory = directory
        self._observability = observability
        self._traced_answer = traced(
            "chain", "ask", self._answer, tags=["hr-assistant"]
        )

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
        environment = self._settings.security.environment
        token = CURRENT_REQUEST_ID.set(request_id)
        try:
            with self._observability.tracer.request(
                request_id, client=client, environment=environment
            ):
                return self._traced_answer(question, history, request_id, client)
        finally:
            CURRENT_REQUEST_ID.reset(token)

    def _answer(
        self, question: str, history: list[dict], request_id: str, client: str
    ) -> AskResponse:
        """Cuerpo de :meth:`answer`, dentro de la traza raíz ``ask``."""
        question, history = self._screen(question, history, request_id, client)
        decision = self._router.choose(question)
        sent = apply_window(history, self._settings.routing.history_window)
        result, decision = self._run_with_fallback(
            decision, question, sent, request_id, client
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
            model_retries=result.usage.retries,
        )
        annotate(
            model=decision.model,
            route=decision.route,
            fallback=decision.route.endswith("+fallback"),
            steps=result.steps,
            stopped=result.stopped,
            cached_calls=response.cached_calls,
            cost_usd=response.cost_usd,
            model_retries=result.usage.retries,
        )
        metrics = self._observability.metrics
        metrics.add_tokens(result.input_tokens, result.output_tokens)
        metrics.add_cost(result.usage.cost_usd)
        return response

    def _run_agent(
        self, decision, question: str, sent: list[dict], request_id: str, client: str
    ) -> AgentResult:
        """Corre el agente con el modelo de la decisión, en el contexto de traza."""
        on_tool_call, on_invalid_reply = self._auditors(request_id, client)
        provider = self._settings.models.provider
        if provider == "gateway":
            provider = "cloudflare-workers-ai"
        agent = Agent(
            self._models[decision.model],
            self._directory,
            self._settings.agent_max_steps,
            model_metadata={
                "ls_model_name": decision.model,
                "ls_provider": provider,
                "route": decision.route,
            },
        )
        return agent.run(
            question,
            sent,
            on_tool_call=on_tool_call,
            on_invalid_reply=on_invalid_reply,
        )

    def _run_with_fallback(
        self, decision, question: str, sent: list[dict], request_id: str, client: str
    ) -> tuple[AgentResult, object]:
        """Si falla el modelo grande, responde con el pequeño (ADR 0018).

        La respuesta lo dice: ``model`` es el pequeño y ``route`` termina en
        ``+fallback``. El log registra ``model_fallback``.

        :return: El resultado y la decisión que de verdad se usó
        :raises app.gateway.ModelUnavailable: Si falla el pequeño, o si no había otro
        """
        try:
            result = self._run_agent(decision, question, sent, request_id, client)
            return result, decision
        except ModelUnavailable as error:
            small, large = self._settings.models.small, self._settings.models.large
            if decision.model != large or small == large:
                raise
            self._observability.logger.emit(
                "model_fallback",
                request_id,
                client=client,
                from_model=large,
                to_model=small,
                error_type=type(error).__name__,
            )
            decision = decision._replace(
                model=small, route=f"{decision.route}+fallback"
            )
            result = self._run_agent(decision, question, sent, request_id, client)
            return result, decision

    def readiness(self) -> tuple[bool, dict]:
        """Estado para ``GET /ready``: datos cargados y circuitos de los modelos.

        La configuración y los precios ya se validaron al arrancar (si no, la aplicación
        no habría arrancado). Está lista si hay directorio y algún modelo no tiene el
        circuito abierto.

        :return: ``(lista, cuerpo de la respuesta)``
        :rtype: tuple[bool, dict]
        """
        models = {
            name: getattr(model, "circuit_state", "closed")
            for name, model in self._models.items()
        }
        directory = "ok" if self._directory.departments() else "empty"
        ready = directory == "ok" and any(state != "open" for state in models.values())
        checks = {"directory": directory, "prices": "ok", "models": models}
        return ready, {"status": "ready" if ready else "not_ready", "checks": checks}

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
        max_chars = self._settings.limits.answer_chars
        clean = sanitize_answer(result.answer, max_chars, CAP_REACHED)
        changes = list(clean.changes)
        found = scan(clean.text)
        text = clean.text
        if found.found:
            self._audit_findings(
                "pii_detected", request_id, client, {"answer": found.types}
            )
            # Las marcas [REDACTED:...] pueden alargar el texto: se vuelve a recortar.
            again = sanitize_answer(found.text, max_chars, CAP_REACHED)
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
        "auth_failures_per_minute": settings.security.auth_failures_per_minute,
        "trusted_proxy_hops": settings.security.trusted_proxy_hops,
        "daily_budget_usd": settings.security.daily_budget_usd,
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


@dataclass(frozen=True)
class Access:
    """Control de acceso de la API (ADR 0010 y 0017).

    :param require_client: Dependencia: cliente de la clave; ``401`` o ``429`` si el
        origen acumula claves fallidas
    :type require_client: Callable
    :param limit_rate: Dependencia: además, límite por minuto y presupuesto diario
    :type limit_rate: Callable
    :param budget: Presupuesto diario; ``/ask`` le suma el costo de cada respuesta
    :type budget: app.security.DailyBudget
    """

    require_client: Callable
    limit_rate: Callable
    budget: DailyBudget


def _too_many(detail: str, retry_after: int) -> HTTPException:
    """Un ``429`` con su motivo y ``Retry-After``."""
    return HTTPException(
        status_code=429, detail=detail, headers={"Retry-After": str(retry_after)}
    )


def _access_dependencies(settings: Settings, observability: Observability) -> Access:
    """Clave por cliente (401); fallas por origen, límite y presupuesto (429).

    Corren antes de validar los campos del cuerpo. Auditan los rechazos sin escribir
    la clave presentada ni la IP: el origen va como huella HMAC (ADR 0017).

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :param observability: Reloj y log
    :type observability: Observability
    :return: Las dependencias y el presupuesto
    :rtype: Access
    """
    security = settings.security
    keys = KeyStore(security.api_keys)
    clock = observability.clock
    limiter = RateLimiter(security.rate_limit_per_minute, clock)
    failures = FailureLimiter(security.auth_failures_per_minute, clock)
    budget = DailyBudget(security.daily_budget_usd, clock)
    fingerprint = OriginFingerprint()
    log = observability.logger

    def require_client(
        request: Request, key: str | None = Security(API_KEY_SCHEME)
    ) -> str:
        """Devuelve el cliente de la clave, o responde 429 (origen bloqueado) o 401."""
        origin = client_origin(
            request.headers.get("X-Forwarded-For"),
            request.client.host if request.client else None,
            security.trusted_proxy_hops,
        )
        retry_after = failures.retry_after(origin)
        if retry_after is not None:
            log.emit(
                "audit",
                request.state.request_id,
                action="auth_throttled",
                origin=fingerprint(origin),
                path=request.url.path,
                retry_after=retry_after,
            )
            raise _too_many("too many failed authentication attempts", retry_after)
        client = keys.client_for(key)
        if client is None:
            failures.record(origin)
            log.emit(
                "audit",
                request.state.request_id,
                action="auth_failed",
                reason="invalid" if key else "missing",
                path=request.url.path,
                origin=fingerprint(origin),
            )
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        request.state.client = client
        return client

    def limit_rate(request: Request, client: str = Depends(require_client)) -> str:
        """Deja pasar al cliente dentro de su límite y su presupuesto; si no, 429."""
        retry_after = limiter.hit(client)
        if retry_after is not None:
            log.emit(
                "audit",
                request.state.request_id,
                action="rate_limited",
                client=client,
                retry_after=retry_after,
            )
            raise _too_many("rate limit exceeded", retry_after)
        retry_after = budget.retry_after(client)
        if retry_after is not None:
            log.emit(
                "audit",
                request.state.request_id,
                action="budget_exceeded",
                client=client,
                budget_usd=budget.limit_usd,
                retry_after=retry_after,
            )
            raise _too_many("daily budget exceeded", retry_after)
        return client

    return Access(require_client, limit_rate, budget)


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

    @app.exception_handler(ModelCircuitOpen)
    async def circuit_open(request: Request, error: ModelCircuitOpen):
        """Circuito abierto: ``503`` rápido con ``Retry-After`` (ADR 0018)."""
        request_id = request.state.request_id
        log.emit(
            "error",
            request_id,
            client=getattr(request.state, "client", None),
            error_type=type(error).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "model temporarily unavailable",
                "request_id": request_id,
            },
            headers={"Retry-After": str(error.retry_after)},
        )

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


def _add_pages(app: FastAPI) -> None:
    """Las dos páginas propias, sin clave: la de RH y el tablero de operación."""
    page = PAGE.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    @app.get(
        "/",
        response_class=HTMLResponse,
        tags=["page"],
        summary="HR Assistant web page",
        description="Page for HR staff; the key lives only in `sessionStorage`.",
    )
    def home() -> str:
        """Sirve la página web del personal de RH."""
        return page

    @app.get(
        "/dashboard",
        response_class=HTMLResponse,
        tags=["operations"],
        summary="Operations dashboard",
        description=(
            "Page with the metrics of this process (it reads `GET /metrics` with the "
            "key of a client, kept only in `sessionStorage`). The page has no data."
        ),
    )
    def operations_dashboard() -> str:
        """Sirve el tablero de operación (ADR 0021)."""
        return dashboard


def _add_routes(
    app: FastAPI,
    settings: Settings,
    service: AskService,
    metrics: Metrics,
    access: Access,
) -> None:
    """Rutas de la API: página y salud sin clave; métricas y preguntas con clave."""
    require_client, limit_rate = access.require_client, access.limit_rate
    ask_request = build_ask_request(settings)
    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["operations"],
        summary="Liveness: the process answers",
        description="Cheap check for the platform; no key and no external calls.",
    )
    def health() -> dict:
        """Indica que el proceso responde y cuál copia contestó."""
        return {"status": "ok", "pid": os.getpid()}

    @app.get(
        "/ready",
        response_model=ReadyResponse,
        responses=api_docs.READY_RESPONSES,
        tags=["operations"],
        summary="Readiness: data loaded and a model available",
        description=(
            "`200` if the directory is loaded and at least one model circuit is not "
            "open; `503` otherwise. Never calls the model."
        ),
    )
    def ready() -> JSONResponse:
        """Dice si la copia puede atender preguntas (ADR 0019)."""
        is_ready, body = service.readiness()
        return JSONResponse(status_code=200 if is_ready else 503, content=body)

    @app.get(
        "/metrics",
        responses=api_docs.METRICS_RESPONSES,
        tags=["operations"],
        summary="Latency, errors, tokens, cost and SLO",
        description="Metrics of this process (each worker has its own).",
        dependencies=[Depends(require_client)],
    )
    def process_metrics() -> dict:
        """Métricas del tráfico de usuarios de este proceso."""
        return metrics.snapshot()

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses=api_docs.ASK_ERRORS,
        tags=["ask"],
        summary="Ask the HR Assistant",
        description=(
            "Routes the question to a model, runs the agent with its read-only tools "
            "and answers with the model, the route and the cost."
        ),
    )
    def ask(
        body: ask_request, request: Request, client: str = Depends(limit_rate)
    ) -> AskResponse:
        """Enruta la pregunta, corre el agente y responde con modelo, ruta y costo."""
        history = [turn.model_dump() for turn in body.history]
        response = service.answer(
            body.question, history, request.state.request_id, client
        )
        access.budget.add(client, response.cost_usd)
        return response


def _enforce_compliance(
    settings: Settings, observability: Observability | None
) -> None:
    """Aplica las reglas de producción; si fallan, audita ``startup_refused``.

    :param settings: Configuración validada
    :type settings: app.config.Settings
    :param observability: Log a usar; si es ``None``, uno a la salida estándar
    :type observability: Observability | None
    :raises app.config.ConfigError: Si producción no cumple sus reglas
    """
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
    _enforce_compliance(settings, observability)
    if directory is None:
        directory = EmployeeDirectory.load(settings.employees_path)
    if observability is None:
        observability = Observability.from_settings(settings)
    owned_client = None
    if http_client is None and model is None and settings.models.provider == "gateway":
        owned_client = http_client = build_http_client(settings.models)
    models = build_models(settings, directory, http_client, model)
    if model is None and settings.models.provider == "gateway":
        models = add_resilience(
            models,
            settings,
            observability.clock,
            lambda name, **fields: observability.logger.emit(name, None, **fields),
        )
    service = AskService(settings, models, directory, observability)
    app = FastAPI(
        title="HR Assistant",
        version=api_docs.VERSION,
        description=api_docs.DESCRIPTION,
        openapi_tags=api_docs.OPENAPI_TAGS,
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
    _add_pages(app)
    _add_routes(app, settings, service, observability.metrics, access)
    # Al final, para quedar por fuera de todo: también los errores llevan cabeceras.
    add_security_headers(
        app,
        {
            "/": PAGE.read_text(encoding="utf-8"),
            "/dashboard": DASHBOARD.read_text(encoding="utf-8"),
        },
        production=settings.security.environment == "production",
    )
    return app


app = create_app()
