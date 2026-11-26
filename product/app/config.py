"""Configuración del HR Assistant desde variables de entorno.

Cada variable tiene su valor por omisión de ``spec/``. Un valor inválido lanza
:class:`ConfigError` y la aplicación no arranca. Los mensajes nombran la variable,
nunca un valor secreto. La clave de LangSmith nunca se guarda (solo se comprueba que
exista); el token de Cloudflare y las claves de ``API_KEYS`` se guardan fuera de
``repr``. Las reglas de producción viven aparte, en ``app.compliance``.
"""

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PROVIDERS = ("fake", "gateway")
DEFAULTS = {
    "MODEL_PROVIDER": "fake",
    "AGENT_MAX_STEPS": "3",
    "MAX_QUESTION_CHARS": "2000",
    "EMPLOYEES_PATH": "data/employees.json",
    "SERVICE_NAME": "hr-assistant",
    "SLO_P95_MS": "2000",
    "LANGSMITH_TRACING": "false",
    "HIDE_INPUTS": "true",
    "HIDE_OUTPUTS": "true",
    "CF_ACCOUNT_ID": "",
    "CF_GATEWAY_ID": "default",
    "CF_API_TOKEN": "",
    "MODEL_SMALL": "@cf/meta/llama-3.1-8b-instruct-fast",
    "MODEL_LARGE": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "ROUTING": "true",
    "ROUTER_MAX_CHARS": "80",
    "HISTORY_WINDOW": "6",
    "MODEL_MAX_TOKENS": "300",
    "MODEL_TEMPERATURE": "0",
    "MODEL_TIMEOUT_SECONDS": "30",
    "CACHE_TTL_SECONDS": "0",
    "PRICES_PATH": "data/model_prices.json",
    "ENVIRONMENT": "development",
    "API_KEYS": "",
    "RATE_LIMIT_PER_MINUTE": "30",
}
BOOLEANS = {"true": True, "false": False}
GATEWAY_VARIABLES = ("CF_ACCOUNT_ID", "CF_GATEWAY_ID", "CF_API_TOKEN")
ENVIRONMENTS = ("development", "production")


class ConfigError(ValueError):
    """Configuración inválida; el mensaje dice qué variable corregir."""


@dataclass(frozen=True)
class ObservabilitySettings:
    """Configuración del log, las métricas y las trazas.

    :param service_name: Campo ``service`` del log
    :type service_name: str
    :param slo_p95_ms: Meta del p95 de latencia en milisegundos
    :type slo_p95_ms: int
    :param tracing_enabled: ``LANGSMITH_TRACING=true`` y la clave presente
    :type tracing_enabled: bool
    :param hide_inputs: No mandar a LangSmith las entradas
    :type hide_inputs: bool
    :param hide_outputs: No mandar a LangSmith las salidas
    :type hide_outputs: bool
    :param tracing_requested: ``LANGSMITH_TRACING=true``, haya o no clave (lo revisa
        compliance)
    :type tracing_requested: bool
    """

    service_name: str
    slo_p95_ms: int
    tracing_enabled: bool
    hide_inputs: bool
    hide_outputs: bool
    tracing_requested: bool = False


@dataclass(frozen=True)
class GatewaySettings:
    """Conexión con Cloudflare AI Gateway (solo se usa con ``gateway``).

    :param account_id: Id de la cuenta de Cloudflare
    :type account_id: str
    :param gateway_id: Nombre del gateway
    :type gateway_id: str
    :param api_token: Token de Workers AI; secreto, fuera de ``repr``
    :type api_token: str
    :param cache_ttl_seconds: Segundos de caché del gateway (0: apagada)
    :type cache_ttl_seconds: int
    :param timeout_seconds: Tiempo máximo de una llamada al modelo
    :type timeout_seconds: float
    """

    account_id: str
    gateway_id: str
    api_token: str = field(repr=False)
    cache_ttl_seconds: int = 0
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ModelSettings:
    """Proveedor, modelos, tope de salida, temperatura y precios.

    :param provider: Modelo que usa el agente (``fake`` o ``gateway``)
    :type provider: str
    :param small: Modelo barato
    :type small: str
    :param large: Modelo capaz
    :type large: str
    :param max_tokens: Tope de tokens de salida por llamada (palanca 3)
    :type max_tokens: int
    :param temperature: Temperatura de cada llamada
    :type temperature: float
    :param prices_path: Ruta absoluta de los precios por modelo
    :type prices_path: pathlib.Path
    :param gateway: Conexión con Cloudflare
    :type gateway: GatewaySettings
    """

    provider: str
    small: str
    large: str
    max_tokens: int
    temperature: float
    prices_path: Path
    gateway: GatewaySettings


@dataclass(frozen=True)
class RoutingSettings:
    """Palancas 1 y 2: enrutamiento por regla y ventana de historial.

    :param enabled: Enrutamiento por pregunta (``ROUTING``)
    :type enabled: bool
    :param max_chars: Preguntas más largas van al modelo capaz
    :type max_chars: int
    :param history_window: Mensajes de historial que se mandan al modelo
    :type history_window: int
    """

    enabled: bool
    max_chars: int
    history_window: int


@dataclass(frozen=True)
class SecuritySettings:
    """Entorno, clientes con su clave y límite de peticiones.

    :param environment: ``development`` o ``production`` (aplica compliance)
    :type environment: str
    :param api_keys: Pares ``(cliente, clave)``; secretos, fuera de ``repr``
    :type api_keys: tuple[tuple[str, str], ...]
    :param rate_limit_per_minute: Peticiones a ``/ask`` por cliente por minuto
    :type rate_limit_per_minute: int
    """

    environment: str
    api_keys: tuple[tuple[str, str], ...] = field(repr=False)
    rate_limit_per_minute: int = 30

    @property
    def clients(self) -> tuple[str, ...]:
        """Nombres de los clientes, sin sus claves.

        :return: Los clientes en el orden de ``API_KEYS``
        :rtype: tuple[str, ...]
        """
        return tuple(client for client, _ in self.api_keys)


@dataclass(frozen=True)
class Settings:
    """Configuración validada del producto.

    :param agent_max_steps: Tope de vueltas del bucle del agente
    :type agent_max_steps: int
    :param max_question_chars: Largo máximo de la pregunta
    :type max_question_chars: int
    :param employees_path: Ruta absoluta del archivo de empleados
    :type employees_path: pathlib.Path
    :param observability: Configuración del log, las métricas y las trazas
    :type observability: ObservabilitySettings
    :param models: Proveedor, modelos, tope de salida, temperatura, precios y gateway
    :type models: ModelSettings
    :param routing: Enrutamiento y ventana de historial
    :type routing: RoutingSettings
    :param security: Entorno, clientes con su clave y límite de peticiones
    :type security: SecuritySettings
    """

    agent_max_steps: int
    max_question_chars: int
    employees_path: Path
    observability: ObservabilitySettings
    models: ModelSettings
    routing: RoutingSettings
    security: SecuritySettings


def _raw(env: Mapping[str, str], name: str) -> str:
    """Valor de la variable sin espacios, o su valor por omisión."""
    return env.get(name, DEFAULTS[name]).strip()


def _integer(env: Mapping[str, str], name: str, minimum: int) -> int:
    """Lee una variable que debe ser un entero mayor o igual a ``minimum``.

    :param env: Variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param minimum: Valor mínimo aceptado
    :type minimum: int
    :return: El valor convertido
    :rtype: int
    :raises ConfigError: Si no es un entero o es menor que ``minimum``
    """
    raw = _raw(env, name)
    try:
        value = int(raw)
    except ValueError:
        value = minimum - 1
    if value < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}, got {raw!r}")
    return value


def _number(
    env: Mapping[str, str], name: str, check: Callable[[float], bool], rule: str
) -> float:
    """Lee una variable numérica que debe cumplir ``check``.

    :param env: Variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param check: Regla que debe cumplir el valor
    :type check: Callable[[float], bool]
    :param rule: La regla en palabras, para el mensaje de error
    :type rule: str
    :return: El valor convertido
    :rtype: float
    :raises ConfigError: Si no es un número finito que cumpla la regla
    """
    raw = _raw(env, name)
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or not check(value):
        raise ConfigError(f"{name} must be {rule}, got {raw!r}")
    return value


def _boolean(env: Mapping[str, str], name: str) -> bool:
    """Lee una variable que debe valer ``true`` o ``false``.

    :param env: Variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :return: El valor convertido
    :rtype: bool
    :raises ConfigError: Si no es ``true`` ni ``false``
    """
    raw = _raw(env, name)
    if raw.lower() not in BOOLEANS:
        raise ConfigError(f"{name} must be true or false, got {raw!r}")
    return BOOLEANS[raw.lower()]


def _path(env: Mapping[str, str], name: str) -> Path:
    """Ruta de la variable; si es relativa, se toma desde la raíz del producto."""
    path = Path(_raw(env, name))
    return path if path.is_absolute() else PRODUCT_ROOT / path


def _observability(env: Mapping[str, str]) -> ObservabilitySettings:
    """Arma la configuración de observabilidad; de la clave solo mira si existe."""
    has_key = bool(env.get("LANGSMITH_API_KEY", "").strip())
    requested = _boolean(env, "LANGSMITH_TRACING")
    return ObservabilitySettings(
        service_name=_raw(env, "SERVICE_NAME") or DEFAULTS["SERVICE_NAME"],
        slo_p95_ms=_integer(env, "SLO_P95_MS", 1),
        tracing_enabled=requested and has_key,
        hide_inputs=_boolean(env, "HIDE_INPUTS"),
        hide_outputs=_boolean(env, "HIDE_OUTPUTS"),
        tracing_requested=requested,
    )


def _gateway(env: Mapping[str, str], provider: str) -> GatewaySettings:
    """Arma la conexión con Cloudflare; con ``gateway`` exige las tres ``CF_*``.

    :raises ConfigError: Si falta una variable ``CF_*`` (el mensaje no lleva valores)
    """
    values = {name: _raw(env, name) for name in GATEWAY_VARIABLES}
    if provider == "gateway":
        for name, value in values.items():
            if not value:
                raise ConfigError(f"{name} is required when MODEL_PROVIDER=gateway")
    return GatewaySettings(
        account_id=values["CF_ACCOUNT_ID"],
        gateway_id=values["CF_GATEWAY_ID"],
        api_token=values["CF_API_TOKEN"],
        cache_ttl_seconds=_integer(env, "CACHE_TTL_SECONDS", 0),
        timeout_seconds=_number(
            env, "MODEL_TIMEOUT_SECONDS", lambda value: value > 0, "a number > 0"
        ),
    )


def _models(env: Mapping[str, str], provider: str) -> ModelSettings:
    """Arma la configuración de modelos, tope de salida, temperatura y precios."""
    return ModelSettings(
        provider=provider,
        small=_raw(env, "MODEL_SMALL") or DEFAULTS["MODEL_SMALL"],
        large=_raw(env, "MODEL_LARGE") or DEFAULTS["MODEL_LARGE"],
        max_tokens=_integer(env, "MODEL_MAX_TOKENS", 1),
        temperature=_number(
            env,
            "MODEL_TEMPERATURE",
            lambda value: 0 <= value <= 2,
            "a number between 0 and 2",
        ),
        prices_path=_path(env, "PRICES_PATH"),
        gateway=_gateway(env, provider),
    )


def _routing(env: Mapping[str, str]) -> RoutingSettings:
    """Arma la configuración de las palancas 1 y 2."""
    return RoutingSettings(
        enabled=_boolean(env, "ROUTING"),
        max_chars=_integer(env, "ROUTER_MAX_CHARS", 1),
        history_window=_integer(env, "HISTORY_WINDOW", 0),
    )


def parse_api_keys(raw: str) -> tuple[tuple[str, str], ...]:
    """Lee ``API_KEYS`` (``cliente:clave,cliente2:clave2``).

    Los mensajes de error dicen qué entrada falla y su largo, nunca su contenido, y
    no encadenan ninguna excepción que pudiera llevarlo.

    :param raw: Valor de ``API_KEYS``; las comas sobrantes se ignoran
    :type raw: str
    :return: Pares ``(cliente, clave)`` en orden
    :rtype: tuple[tuple[str, str], ...]
    :raises ConfigError: Si una entrada no es ``cliente:clave`` o se repite
    """
    pairs: list[tuple[str, str]] = []
    for index, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        client, _, key = (part.strip() for part in entry.partition(":"))
        if not client or not key:
            raise ConfigError(
                f"API_KEYS entry {index} is invalid (length {len(entry)}): "
                "use client:key"
            )
        if client in (name for name, _ in pairs):
            raise ConfigError(f"API_KEYS client {client!r} is repeated")
        if key in (known for _, known in pairs):
            raise ConfigError(f"API_KEYS entry {index} repeats a key")
        pairs.append((client, key))
    return tuple(pairs)


def _security(env: Mapping[str, str]) -> SecuritySettings:
    """Arma el entorno, los clientes con su clave y el límite de peticiones."""
    environment = _raw(env, "ENVIRONMENT").lower()
    if environment not in ENVIRONMENTS:
        allowed = ", ".join(ENVIRONMENTS)
        raise ConfigError(f"ENVIRONMENT must be one of: {allowed}; got {environment!r}")
    return SecuritySettings(
        environment=environment,
        api_keys=parse_api_keys(env.get("API_KEYS", DEFAULTS["API_KEYS"])),
        rate_limit_per_minute=_integer(env, "RATE_LIMIT_PER_MINUTE", 1),
    )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Arma la configuración desde las variables de entorno.

    :param env: Variables de entorno; si es ``None``, las del proceso
    :type env: Mapping[str, str] | None
    :return: La configuración validada
    :rtype: Settings
    :raises ConfigError: Si alguna variable tiene un valor inválido
    """
    env = os.environ if env is None else env
    provider = _raw(env, "MODEL_PROVIDER")
    if provider not in MODEL_PROVIDERS:
        allowed = ", ".join(MODEL_PROVIDERS)
        raise ConfigError(f"MODEL_PROVIDER must be one of: {allowed}; got {provider!r}")
    return Settings(
        agent_max_steps=_integer(env, "AGENT_MAX_STEPS", 1),
        max_question_chars=_integer(env, "MAX_QUESTION_CHARS", 1),
        employees_path=_path(env, "EMPLOYEES_PATH"),
        observability=_observability(env),
        models=_models(env, provider),
        routing=_routing(env),
        security=_security(env),
    )
