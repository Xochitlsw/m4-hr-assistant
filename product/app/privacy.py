"""Datos personales y secretos: detección y redacción (ADR 0014).

Un mismo detector sirve para la entrada, la salida y las trazas:

- **Secretos** (claves privadas, JWT, llaves de AWS, tokens de GitHub, claves de
  LangSmith, ``Bearer`` y asignaciones como ``password: ...``): en la entrada bloquean
  la petición; en la salida y en las trazas se redactan.
- **Datos personales** (CURP, RFC, correo, tarjeta con Luhn, CLABE, NSS y teléfono de
  México): se redactan como ``[REDACTED:TIPO]`` antes de llegar al modelo, en la
  respuesta y en las trazas.

Los datos del directorio (id, nombre, puesto, departamento, fecha de ingreso y estado)
no tienen ninguna de esas formas: son el propósito del asistente y nunca se redactan.

El detector mira :func:`app.sanitize.detection_view`, una copia sin caracteres
invisibles, en NFKC y con dígitos ASCII. Si encuentra algo, el texto redactado sale de
esa copia; si no, el texto vuelve intacto. Es un detector por patrones: lo que no
tiene forma reconocible (un domicilio, un nombre suelto) no se detecta.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.sanitize import BlockedRequest, detection_view

SECRETS = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "GITHUB_TOKEN",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    ),
    # pt: personal, sk: de servicio, tr: Tracing Only (la de mínimo privilegio).
    ("LANGSMITH_KEY", re.compile(r"\blsv2_(?:pt|sk|tr)_[A-Za-z0-9_]{20,}")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    (
        "CREDENTIAL",
        re.compile(
            r"(?i)\b(?:api[_ -]?key|token|secret|password|passwd|contrase(?:ñ|n)a"
            r"|clave)\b\s*[:=]\s*\S{6,}"
        ),
    ),
)
# El orden importa: CURP antes que RFC (una CURP empieza como un RFC) y la tarjeta
# (con Luhn) antes que CLABE, NSS y teléfono, que son tramos de dígitos más cortos.
PII = (
    ("CURP", re.compile(r"(?i)\b[A-Z]{4}[0-9]{6}[HMX][A-Z]{5}[A-Z0-9][0-9]\b")),
    ("RFC", re.compile(r"(?i)\b[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("CARD", re.compile(r"(?<![0-9])[0-9](?:[ -]?[0-9]){12,18}(?![0-9])")),
    ("CLABE", re.compile(r"(?<![0-9])[0-9]{18}(?![0-9])")),
    ("NSS", re.compile(r"(?<![0-9])[0-9]{11}(?![0-9])")),
    (
        "PHONE",
        re.compile(
            r"(?<![\w-])(?:\+?52[ -]?)?\(?[0-9]{2,3}\)?[ -]?[0-9]{3,4}[ -]?[0-9]{4}"
            r"(?![\w-])"
        ),
    ),
)


def passes_luhn(number: str) -> bool:
    """Indica si los dígitos pasan la suma de Luhn de las tarjetas.

    :param number: Número con o sin espacios y guiones
    :type number: str
    :return: ``True`` si la suma es múltiplo de 10
    :rtype: bool
    """
    total = 0
    for position, char in enumerate(reversed(re.sub(r"[ -]", "", number))):
        digit = int(char)
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


# Solo una tarjeta que pasa Luhn se marca como tarjeta; si no, la ven los siguientes.
VALIDATORS: dict[str, Callable[[str], bool]] = {"CARD": passes_luhn}


class SecretFound(BlockedRequest):
    """La pregunta o el historial traen un secreto; la API responde ``400``."""

    DETAIL = "request contains a secret; remove it and ask again"


def merge_counts(counts: Iterable[dict[str, int]]) -> dict[str, int]:
    """Suma conteos por tipo (por ejemplo, de todos los mensajes del historial).

    :param counts: Conteos ``{tipo: cantidad}``
    :type counts: Iterable[dict[str, int]]
    :return: La suma por tipo
    :rtype: dict[str, int]
    """
    total: dict[str, int] = {}
    for count in counts:
        for kind, amount in count.items():
            total[kind] = total.get(kind, 0) + amount
    return total


@dataclass(frozen=True)
class Scan:
    """Resultado de revisar un texto.

    :param text: Texto redactado; el original si no se encontró nada
    :type text: str
    :param pii: Datos personales encontrados, por tipo
    :type pii: dict[str, int]
    :param secrets: Secretos encontrados, por tipo
    :type secrets: dict[str, int]
    """

    text: str
    pii: dict[str, int] = field(default_factory=dict)
    secrets: dict[str, int] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        """Indica si encontró algo.

        :return: ``True`` si hubo al menos un dato personal o un secreto
        :rtype: bool
        """
        return bool(self.pii or self.secrets)

    @property
    def types(self) -> dict[str, int]:
        """Todo lo encontrado, por tipo, para la auditoría (nunca los valores).

        :return: ``{tipo: cantidad}``
        :rtype: dict[str, int]
        """
        return {**self.secrets, **self.pii}


def _redact(text: str, kind: str, pattern: re.Pattern, counts: dict) -> str:
    """Reemplaza cada coincidencia válida por ``[REDACTED:TIPO]`` y la cuenta."""
    validate = VALIDATORS.get(kind)

    def replace(match: re.Match) -> str:
        if validate is not None and not validate(match.group()):
            return match.group()
        counts[kind] = counts.get(kind, 0) + 1
        return f"[REDACTED:{kind}]"

    return pattern.sub(replace, text)


def scan(text: str) -> Scan:
    """Busca secretos y datos personales y los redacta.

    :param text: Texto de la pregunta, del historial, de la respuesta o de una traza
    :type text: str
    :return: El texto redactado y lo encontrado por tipo
    :rtype: Scan
    """
    redacted = detection_view(text)
    secrets: dict[str, int] = {}
    pii: dict[str, int] = {}
    for kind, pattern in SECRETS:
        redacted = _redact(redacted, kind, pattern, secrets)
    for kind, pattern in PII:
        redacted = _redact(redacted, kind, pattern, pii)
    if not (secrets or pii):
        return Scan(text)
    return Scan(redacted, pii, secrets)


def redact_tree(value: Any) -> Any:
    """Redacta cada texto de una estructura JSON (el ``anonymizer`` de LangSmith).

    :param value: Diccionario, lista o valor ya serializados
    :type value: Any
    :return: La misma estructura con cada texto redactado; lo demás, intacto
    :rtype: Any
    """
    if isinstance(value, str):
        return scan(value).text
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    return value
