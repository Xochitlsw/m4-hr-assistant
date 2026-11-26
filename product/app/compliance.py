"""Compliance como código: reglas de producción (ADR 0011; trazas, ADR 0014).

Con ``ENVIRONMENT=production`` la aplicación no arranca si alguna regla falla. Cada
regla está en :func:`violations` y su prueba en ``tests/test_compliance.py``; la
tabla está en ``doc/compliance-checklist.md``. Los mensajes nombran clientes y
variables, nunca una clave.
"""

from app.config import ConfigError, Settings

MIN_KEY_LENGTH = 16
# Producción no puede quitar los topes de entrada, agencia y consumo (ADR 0016).
CEILINGS = (
    ("MAX_BODY_BYTES", 1_048_576, lambda settings: settings.limits.body_bytes),
    ("MAX_HISTORY_MESSAGES", 50, lambda settings: settings.limits.history_messages),
    ("AGENT_MAX_STEPS", 5, lambda settings: settings.agent_max_steps),
    ("MODEL_MAX_TOKENS", 1024, lambda settings: settings.models.max_tokens),
)


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
    for name, ceiling, read in CEILINGS:
        if read(settings) > ceiling:
            found.append(f"{name} must be at most {ceiling} in production")
    if settings.security.daily_budget_usd <= 0:
        found.append("CLIENT_DAILY_BUDGET_USD must be greater than 0 in production")
    observability = settings.observability
    modes = (observability.inputs_privacy, observability.outputs_privacy)
    if observability.tracing_requested and "clear" in modes:
        found.append(
            "HIDE_INPUTS and HIDE_OUTPUTS must be true (hide) or redact, never false, "
            "when LANGSMITH_TRACING=true"
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
