"""Compliance como código: reglas de producción (ADR 0011).

Con ``ENVIRONMENT=production`` la aplicación no arranca si alguna regla falla. Cada
regla está en :func:`violations` y su prueba en ``tests/test_compliance.py``; la
tabla está en ``doc/compliance-checklist.md``. Los mensajes nombran clientes y
variables, nunca una clave.
"""

from app.config import ConfigError, Settings

MIN_KEY_LENGTH = 16


def violations(settings: Settings) -> list[str]:
    """Reglas de producción que esta configuración no cumple.

    :param settings: Configuración ya validada
    :type settings: app.config.Settings
    :return: Una frase por regla incumplida; vacía si cumple todas
    :rtype: list[str]
    """
    found = []
    if settings.models.provider == "fake":
        found.append("MODEL_PROVIDER must not be fake")
    keys = settings.security.api_keys
    if not keys:
        found.append("API_KEYS must define at least one client")
    for client, key in keys:
        if len(key) < MIN_KEY_LENGTH:
            found.append(
                f"API_KEYS key of client {client!r} must have at least "
                f"{MIN_KEY_LENGTH} characters"
            )
    observability = settings.observability
    hidden = observability.hide_inputs and observability.hide_outputs
    if observability.tracing_requested and not hidden:
        found.append(
            "HIDE_INPUTS and HIDE_OUTPUTS must be true when LANGSMITH_TRACING=true"
        )
    return found


def enforce(settings: Settings) -> None:
    """Impide arrancar en producción si alguna regla falla.

    :param settings: Configuración ya validada
    :type settings: app.config.Settings
    :raises app.config.ConfigError: Con ``ENVIRONMENT=production`` y alguna regla
        incumplida; el mensaje las reúne todas
    """
    if settings.security.environment != "production":
        return
    found = violations(settings)
    if found:
        raise ConfigError(
            "ENVIRONMENT=production refuses to start: " + "; ".join(found)
        )
