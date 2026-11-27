"""Detección de prompt injection en la pregunta y el historial (ADR 0016).

El historial lo manda el cliente: puede traer turnos ``assistant`` falsificados, por
ejemplo una llamada a una herramienta en el protocolo JSON del agente. Por eso se
revisan la pregunta y cada mensaje del historial, de los dos roles.

Son heurísticas en español e inglés, por reglas con nombre. Miran una copia del texto
sin caracteres invisibles, en NFKC, sin acentos y en minúsculas
(:func:`detection_text`), para que «ig», U+200B, «nora» o «ＩＧＮＯＲＡ» no las esquiven.
Una coincidencia bloquea la petición con ``400`` (:class:`InjectionFound`) y se audita
con el nombre de las reglas, nunca con el texto.

Es una capa más, no la única: el agente solo ejecuta dos herramientas de solo lectura
con argumentos validados, tiene tope de pasos y su salida se limpia (ADR 0013).
"""

import re
import unicodedata

from app.sanitize import BlockedRequest, detection_view

OVERRIDE_VERBS = (
    r"ignora|ignorar|ignore|olvida|olvidate|forget|omite|omit|descarta|disregard"
    r"|anula|override|salta|skip|bypass|evita"
)
OVERRIDE_TARGETS = (
    r"instrucciones|instruccion|reglas|indicaciones|restricciones|politicas"
    r"|instructions|instruction|rules|guidelines|directives|restrictions|prompt"
)
REVEAL_VERBS = (
    r"revela|revelame|muestra|muestrame|imprime|dime|repite|copia|escribe"
    r"|reveal|show|print|tell me|repeat|output|dump|leak|display"
)
REVEAL_TARGETS = (
    r"system prompt|prompt|tus instrucciones|your instructions|api ?key|apikey"
    r"|contrasenas?|passwords?|credenciales|credentials|secretos?|secrets?"
    r"|variables de entorno|environment variables|env vars"
)
# Reglas en orden fijo: el resultado sale en este orden y sin repetir.
RULES = (
    (
        "override",
        re.compile(rf"\b(?:{OVERRIDE_VERBS})\b.{{0,40}}\b(?:{OVERRIDE_TARGETS})\b"),
    ),
    (
        "system_prompt",
        re.compile(
            r"system prompt|prompt (?:del|de) sistema|instrucciones (?:del|de) sistema"
            r"|mensaje (?:del|de) sistema|system message|instrucciones iniciales"
            r"|initial instructions"
        ),
    ),
    (
        "role_switch",
        re.compile(
            r"\b(?:actua como|act as|you are now|from now on|ahora eres"
            r"|desde ahora eres"
            r"|pretend (?:to be|you are)|finge (?:que eres|ser)|roleplay as"
            r"|a partir de ahora (?:eres|seras|vas a ser|actua|responde))\b"
        ),
    ),
    (
        "reveal",
        re.compile(rf"\b(?:{REVEAL_VERBS})\b.{{0,40}}\b(?:{REVEAL_TARGETS})\b"),
    ),
    # Alguien escribe el protocolo JSON del agente: un turno o una herramienta falsos.
    ("protocol", re.compile(r'"\s*(?:tool|answer|args)\s*"\s*:')),
    (
        "fake_role",
        re.compile(
            r"(?:^|\n)\s*(?:system|sistema|assistant|asistente|developer)\s*:"
            r"|<\|?\s*(?:system|im_start|im_end)\s*\|?>|\[/?(?:inst|system)\]"
            r"|#{2,}\s*(?:instruction|system)"
        ),
    ),
    (
        "jailbreak",
        re.compile(
            r"\b(?:jailbreak|modo desarrollador|developer mode|do anything now"
            r"|dan mode|modo dios|god mode)\b"
        ),
    ),
)


class InjectionFound(BlockedRequest):
    """La pregunta o el historial parecen una instrucción para el modelo."""

    DETAIL = "request looks like a prompt injection; ask about employees"


def detection_text(text: str) -> str:
    """Copia para las reglas: :func:`app.sanitize.detection_view`, sin acentos y en
    minúsculas.

    :param text: Texto original
    :type text: str
    :return: La copia normalizada
    :rtype: str
    """
    decomposed = unicodedata.normalize("NFKD", detection_view(text))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return plain.lower()


def detect_injection(text: str) -> tuple[str, ...]:
    """Nombres de las reglas que el texto cumple, en el orden de :data:`RULES`.

    :param text: Pregunta o contenido de un mensaje del historial
    :type text: str
    :return: Reglas que coinciden; vacía si ninguna
    :rtype: tuple[str, ...]
    """
    plain = detection_text(text)
    return tuple(name for name, pattern in RULES if pattern.search(plain))


def merge_rules(found: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Une las reglas de varios mensajes, sin repetir y en el orden de :data:`RULES`.

    :param found: Reglas de cada mensaje
    :type found: list[tuple[str, ...]]
    :return: Las reglas que aparecen en alguno
    :rtype: tuple[str, ...]
    """
    seen = {name for names in found for name in names}
    return tuple(name for name, _ in RULES if name in seen)
