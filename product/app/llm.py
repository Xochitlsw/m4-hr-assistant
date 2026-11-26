"""Interfaz del modelo y modelo simulado determinista (``MODEL_PROVIDER=fake``).

El agente solo conoce :class:`ChatModel`: cambiar de modelo es agregar otra
implementación de ``complete(messages)`` y registrarla en :func:`build_model`.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.config import ConfigError, Settings
from app.directory import EmployeeDirectory, normalize

TOOL_RESULT_PREFIX = "Resultado de la herramienta: "
EMPLOYEE_ID = re.compile(r"\bEMP-\d{4}\b")
GUIDANCE = "Pregúntame por un empleado (EMP-0001) o un departamento."


@dataclass(frozen=True)
class Completion:
    """Lo que devuelve el modelo en una llamada.

    :param text: Respuesta del modelo (un objeto JSON en texto)
    :type text: str
    :param input_tokens: Tokens de entrada de la llamada
    :type input_tokens: int
    :param output_tokens: Tokens de salida de la llamada
    :type output_tokens: int
    """

    text: str
    input_tokens: int
    output_tokens: int


class ChatModel(Protocol):
    """Interfaz que cumple cualquier modelo del agente."""

    def complete(self, messages: list[dict]) -> Completion:
        """Responde a la conversación con un único objeto JSON en texto.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta y sus tokens
        :rtype: Completion
        """


def count_words(text: str) -> int:
    """Cuenta palabras separadas por espacios (tokens aproximados).

    :param text: Texto a contar
    :type text: str
    :return: Número de palabras
    :rtype: int
    """
    return len(text.split())


class FakeModel:
    """Modelo simulado: decide con reglas sobre el último mensaje, sin red ni costo.

    :param departments: Departamentos que existen en los datos
    :type departments: Iterable[str]
    """

    def __init__(self, departments: Iterable[str]):
        self._departments = {normalize(name): name for name in departments}

    def complete(self, messages: list[dict]) -> Completion:
        """Aplica las reglas de la especificación al último mensaje.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta en JSON y los tokens contados por palabras
        :rtype: Completion
        """
        last = messages[-1]["content"] if messages else ""
        text = json.dumps(self._decide(last), ensure_ascii=False)
        input_tokens = sum(count_words(message["content"]) for message in messages)
        return Completion(text, input_tokens, count_words(text))

    def _decide(self, last: str) -> dict:
        """Elige la respuesta según las cuatro reglas, en orden."""
        if last.startswith(TOOL_RESULT_PREFIX):
            result = last[len(TOOL_RESULT_PREFIX):]
            return {"answer": f"Según el directorio: {result}"}
        match = EMPLOYEE_ID.search(last)
        if match:
            return {"tool": "get_employee", "args": {"employee_id": match.group()}}
        department = self._mentioned_department(last)
        if department:
            return {"tool": "search_by_department", "args": {"department": department}}
        return {"answer": GUIDANCE}

    def _mentioned_department(self, text: str) -> str | None:
        """Devuelve el departamento que el texto menciona como palabra completa."""
        normalized = normalize(text)
        for key, name in self._departments.items():
            if re.search(rf"\b{re.escape(key)}\b", normalized):
                return name
        return None


def build_model(settings: Settings, directory: EmployeeDirectory) -> ChatModel:
    """Crea el modelo que pide ``MODEL_PROVIDER``.

    :param settings: Configuración validada
    :type settings: Settings
    :param directory: Directorio, del que el modelo simulado toma los departamentos
    :type directory: EmployeeDirectory
    :return: El modelo listo para el agente
    :rtype: ChatModel
    :raises ConfigError: Si el proveedor no existe
    """
    if settings.model_provider != "fake":
        raise ConfigError(f"MODEL_PROVIDER not supported: {settings.model_provider!r}")
    return FakeModel(directory.departments())
