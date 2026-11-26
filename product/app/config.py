"""Configuración del HR Assistant desde variables de entorno.

Cada variable tiene su valor por omisión de ``spec/01-architecture.md``. Un valor
inválido lanza :class:`ConfigError` y la aplicación no arranca.
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
}


class ConfigError(ValueError):
    """Configuración inválida; el mensaje dice qué variable corregir."""


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
    """

    model_provider: str
    agent_max_steps: int
    max_question_chars: int
    employees_path: Path


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
    raw = env.get(name, DEFAULTS[name]).strip()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ConfigError(f"{name} must be an integer >= 1, got {raw!r}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Arma la configuración desde las variables de entorno.

    :param env: Variables de entorno; si es ``None``, las del proceso
    :type env: Mapping[str, str] | None
    :return: La configuración validada
    :rtype: Settings
    :raises ConfigError: Si alguna variable tiene un valor inválido
    """
    env = os.environ if env is None else env
    provider = env.get("MODEL_PROVIDER", DEFAULTS["MODEL_PROVIDER"]).strip()
    if provider not in MODEL_PROVIDERS:
        allowed = ", ".join(MODEL_PROVIDERS)
        raise ConfigError(f"MODEL_PROVIDER must be one of: {allowed}; got {provider!r}")
    path = Path(env.get("EMPLOYEES_PATH", DEFAULTS["EMPLOYEES_PATH"]).strip())
    if not path.is_absolute():
        path = PRODUCT_ROOT / path
    return Settings(
        model_provider=provider,
        agent_max_steps=_positive_int(env, "AGENT_MAX_STEPS"),
        max_question_chars=_positive_int(env, "MAX_QUESTION_CHARS"),
        employees_path=path,
    )
