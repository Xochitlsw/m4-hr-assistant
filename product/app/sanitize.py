"""Texto que entra y sale de la API: caracteres prohibidos y limpieza (ADR 0013).

- **Entrada**: la pregunta y el historial no pueden traer caracteres de control (salvo
  tabulador y saltos de línea), marcas de dirección bidireccional ni caracteres de
  etiqueta Unicode, que sirven para esconder texto a quien lo lee (el modelo sí lo lee).
  Se rechazan con ``422``: no se adivina qué quiso decir el cliente.
- **Salida**: la respuesta del modelo se limpia en vez de rechazarse, porque la persona
  ya espera una respuesta: se quitan esos caracteres y las etiquetas HTML, y se recorta
  al largo máximo. Si no queda nada, se usa un texto seguro.

Los detectores (datos personales, secretos e inyección) no miran el texto tal cual sino
:func:`detection_view`: sin caracteres de formato invisibles, en NFKC y con dígitos
ASCII. Así un carácter invisible o un dígito de otro alfabeto no los esquiva.

Este módulo no escribe en el log: quien lo usa audita los cambios, nunca el texto.
"""

import re
import unicodedata
from dataclasses import dataclass

ALLOWED_CONTROLS = frozenset(map(chr, (0x09, 0x0A, 0x0D)))
# Marcas que invierten o aíslan la dirección del texto (U+202A a U+202E y U+2066 a
# U+2069).
BIDI_CONTROLS = frozenset(
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
# Caracteres de etiqueta (U+E0000 a U+E007F): no se ven, pero el modelo los lee.
TAG_CHARACTERS = frozenset(chr(code) for code in range(0xE0000, 0xE0080))
# Solo lo que parece una etiqueta: «<» pegado a una letra o a «/» («< 5000» se queda).
HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
TRUNCATION_MARK = " [respuesta recortada]"


def is_forbidden(char: str) -> bool:
    """Indica si un carácter no se acepta en texto de personas.

    :param char: Un carácter
    :type char: str
    :return: ``True`` para controles (categoría ``Cc``) que no sean tabulador o salto
        de línea, para marcas bidireccionales y para caracteres de etiqueta
    :rtype: bool
    """
    if char in ALLOWED_CONTROLS:
        return False
    if char in BIDI_CONTROLS or char in TAG_CHARACTERS:
        return True
    return unicodedata.category(char) == "Cc"


def _ascii_digit(char: str) -> str:
    """El dígito ASCII de un dígito de cualquier alfabeto; otro carácter, igual."""
    value = unicodedata.decimal(char, None)
    return char if value is None else str(value)


def detection_view(text: str) -> str:
    """Copia del texto para los detectores, que no se muestra a nadie.

    Quita los caracteres de formato (categoría ``Cf``: espacio de ancho cero, unión de
    palabras, guion suave, marcas de dirección y etiquetas), normaliza con NFKC (por
    ejemplo, dígitos de ancho completo) y convierte los dígitos de otros alfabetos en
    ASCII. Así «ig», U+200B, «nora» se lee «ignora».

    :param text: Texto original
    :type text: str
    :return: La copia normalizada
    :rtype: str
    """
    visible = "".join(char for char in text if unicodedata.category(char) != "Cf")
    normalized = unicodedata.normalize("NFKC", visible)
    return "".join(
        char if char.isascii() else _ascii_digit(char) for char in normalized
    )


def has_forbidden_chars(text: str) -> bool:
    """Indica si el texto trae algún carácter prohibido.

    :param text: Texto de la pregunta o de un mensaje del historial
    :type text: str
    :return: ``True`` si hay al menos uno
    :rtype: bool
    """
    return any(is_forbidden(char) for char in text)


@dataclass(frozen=True)
class CleanAnswer:
    """Respuesta lista para salir y lo que se le cambió.

    :param text: Texto limpio, de ``max_chars`` caracteres como máximo
    :type text: str
    :param changes: Cambios aplicados, en orden: ``control_chars``, ``html``,
        ``truncated`` y ``empty``; vacío si no hizo falta ninguno
    :type changes: tuple[str, ...]
    """

    text: str
    changes: tuple[str, ...]


def sanitize_answer(text: str, max_chars: int, fallback: str) -> CleanAnswer:
    """Limpia la respuesta del modelo antes de devolverla.

    :param text: Respuesta del modelo
    :type text: str
    :param max_chars: Largo máximo, incluida la marca de recorte
    :type max_chars: int
    :param fallback: Texto seguro si no queda nada después de limpiar
    :type fallback: str
    :return: El texto limpio y los cambios aplicados
    :rtype: CleanAnswer
    """
    changes = []
    without_controls = "".join(char for char in text if not is_forbidden(char))
    if without_controls != text:
        changes.append("control_chars")
    without_tags = HTML_TAG.sub("", without_controls)
    if without_tags != without_controls:
        changes.append("html")
    clean = without_tags.strip()
    if len(clean) > max_chars:
        clean = clean[: max_chars - len(TRUNCATION_MARK)].rstrip() + TRUNCATION_MARK
        changes.append("truncated")
    if not clean:
        clean = fallback
        changes.append("empty")
    return CleanAnswer(clean, tuple(changes))
