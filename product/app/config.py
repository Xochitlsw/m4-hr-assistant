"""Configuración del HR Assistant desde variables de entorno.

Cada variable tiene su valor por omisión de ``spec/``. Un valor inválido lanza
:class:`ConfigError` y la aplicación no arranca. La clave de LangSmith nunca se
guarda: solo se comprueba que exista.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PROVIDERS = ("fake",)
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
}
BOOLEANS = {"true": True, "false": False}


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
    """

    service_name: str
    slo_p95_ms: int
    tracing_enabled: bool
    hide_inputs: bool
    hide_outputs: bool


@dataclass(frozen=True)
class Settings:
    """Configuración validada del producto.

    :param model_provider: Modelo que usa el agente (``fake``)
    :type model_provider: str
    :param agent_max_steps: Tope de vueltas del bucle del agente
    :type agent_max_steps: int
    :param max_question_chars: Largo máximo de la pregunta
    :type max_question_chars: int
    :param employees_path: Ruta absoluta del archivo de empleados
    :type employees_path: pathlib.Path
    :param observability: Configuración del log, las métricas y las trazas
    :type observability: ObservabilitySettings
    """

    model_provider: str
    agent_max_steps: int
    max_question_chars: int
    employees_path: Path
    observability: ObservabilitySettings


def _raw(env: Mapping[str, str], name: str) -> str:
    """Valor de la variable sin espacios, o su valor por omisión."""
    return env.get(name, DEFAULTS[name]).strip()


def _positive_int(env: Mapping[str, str], name: str) -> int:
    """Lee una variable que debe ser un entero mayor o igual a 1.

    :param env: Variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :return: El valor convertido
    :rtype: int
    :raises ConfigError: Si no es un entero o es menor que 1
    """
    raw = _raw(env, name)
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ConfigError(f"{name} must be an integer >= 1, got {raw!r}")
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


def _observability(env: Mapping[str, str]) -> ObservabilitySettings:
    """Arma la configuración de observabilidad; de la clave solo mira si existe."""
    has_key = bool(env.get("LANGSMITH_API_KEY", "").strip())
    return ObservabilitySettings(
        service_name=_raw(env, "SERVICE_NAME") or DEFAULTS["SERVICE_NAME"],
        slo_p95_ms=_positive_int(env, "SLO_P95_MS"),
        tracing_enabled=_boolean(env, "LANGSMITH_TRACING") and has_key,
        hide_inputs=_boolean(env, "HIDE_INPUTS"),
        hide_outputs=_boolean(env, "HIDE_OUTPUTS"),
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
    path = Path(_raw(env, "EMPLOYEES_PATH"))
    if not path.is_absolute():
        path = PRODUCT_ROOT / path
    return Settings(
        model_provider=provider,
        agent_max_steps=_positive_int(env, "AGENT_MAX_STEPS"),
        max_question_chars=_positive_int(env, "MAX_QUESTION_CHARS"),
        employees_path=path,
        observability=_observability(env),
    )
