"""Agente del HR Assistant: bucle con tope sobre el protocolo JSON del modelo.

En cada paso el modelo pide una herramienta (``{"tool", "args"}``) o responde
(``{"answer"}``). Solo se ejecutan las herramientas de la lista blanca, con sus
argumentos validados; una respuesta fuera del protocolo consume un paso y se le
devuelve al modelo como error.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.directory import EmployeeDirectory
from app.llm import TOOL_RESULT_PREFIX, ChatModel

CAP_REACHED = "No pude completar la respuesta. Intenta con una pregunta más concreta."
SYSTEM_PROMPT = (
    "Eres el asistente de Recursos Humanos. Responde siempre un único objeto JSON:\n"
    '- {"tool": "get_employee", "args": {"employee_id": "EMP-0004"}} para buscar '
    "un empleado por id.\n"
    '- {"tool": "search_by_department", "args": {"department": "Finanzas"}} para '
    "listar un departamento.\n"
    '- {"answer": "<respuesta en español>"} para responder.\n'
    "Usa solo los datos que devuelvan las herramientas."
)


@dataclass(frozen=True)
class AgentResult:
    """Resultado de una pregunta.

    :param answer: Respuesta para la persona
    :type answer: str
    :param steps: Llamadas al modelo que se hicieron
    :type steps: int
    :param tools_used: Herramientas ejecutadas, en orden
    :type tools_used: list[str]
    :param input_tokens: Tokens de entrada sumados de todos los pasos
    :type input_tokens: int
    :param output_tokens: Tokens de salida sumados de todos los pasos
    :type output_tokens: int
    """

    answer: str
    steps: int
    tools_used: list[str]
    input_tokens: int
    output_tokens: int


class InvalidReply(ValueError):
    """El modelo respondió algo fuera del protocolo."""


class Agent:
    """Agente con dos herramientas de solo lectura y tope de pasos.

    :param model: Modelo que decide cada paso
    :type model: ChatModel
    :param directory: Directorio de empleados (solo lectura)
    :type directory: EmployeeDirectory
    :param max_steps: Tope de llamadas al modelo por pregunta
    :type max_steps: int
    """

    def __init__(self, model: ChatModel, directory: EmployeeDirectory, max_steps: int):
        self._model = model
        self._directory = directory
        self._max_steps = max_steps
        self._tools = {
            "get_employee": ("employee_id", self._get_employee),
            "search_by_department": ("department", self._search_by_department),
        }

    def run(self, question: str, history: Sequence[dict] = ()) -> AgentResult:
        """Responde una pregunta con el historial que manda el cliente.

        :param question: Pregunta de la persona
        :type question: str
        :param history: Turnos previos con ``role`` y ``content``
        :type history: Sequence[dict]
        :return: Respuesta, pasos, herramientas usadas y tokens
        :rtype: AgentResult
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [
            {"role": turn["role"], "content": turn["content"]} for turn in history
        ]
        messages.append({"role": "user", "content": question})
        tools_used: list[str] = []
        tokens = [0, 0]
        for step in range(1, self._max_steps + 1):
            completion = self._model.complete(messages)
            tokens[0] += completion.input_tokens
            tokens[1] += completion.output_tokens
            answer, tool_result = self._handle(completion.text, tools_used)
            if answer is not None:
                return AgentResult(answer, step, tools_used, *tokens)
            messages.append({"role": "assistant", "content": completion.text})
            messages.append(
                {"role": "user", "content": TOOL_RESULT_PREFIX + tool_result}
            )
        return AgentResult(CAP_REACHED, self._max_steps, tools_used, *tokens)

    def _handle(self, text: str, tools_used: list[str]) -> tuple[str | None, str]:
        """Interpreta una respuesta del modelo.

        :return: ``(respuesta, "")`` si el modelo respondió, o ``(None, resultado)``
            con el resultado de la herramienta o el error del protocolo
        """
        try:
            reply = self._parse(text)
        except InvalidReply as error:
            return None, f"error: {error}"
        if "answer" in reply:
            return reply["answer"], ""
        name = reply["tool"]
        argument_name, tool = self._tools[name]
        tools_used.append(name)
        return None, tool(reply["args"][argument_name])

    def _parse(self, text: str) -> dict:
        """Valida que la respuesta cumpla el protocolo y la lista blanca.

        :raises InvalidReply: Si no es JSON, no es un objeto o no cumple el protocolo
        """
        try:
            reply = json.loads(text)
        except ValueError as error:
            raise InvalidReply("la respuesta no es JSON válido") from error
        if isinstance(reply, dict) and set(reply) == {"answer"}:
            if isinstance(reply["answer"], str):
                return reply
        if not isinstance(reply, dict) or set(reply) != {"tool", "args"}:
            raise InvalidReply('responde solo {"tool", "args"} o {"answer"}')
        name, args = reply["tool"], reply["args"]
        if not isinstance(name, str) or name not in self._tools:
            raise InvalidReply(f"herramienta desconocida: {name}")
        argument_name = self._tools[name][0]
        if not isinstance(args, dict) or set(args) != {argument_name}:
            raise InvalidReply(f'{name} recibe solo {{"{argument_name}": "..."}}')
        if not isinstance(args[argument_name], str):
            raise InvalidReply(f"{argument_name} debe ser texto")
        return reply

    def _get_employee(self, employee_id: str) -> str:
        """Herramienta ``get_employee``: el empleado en JSON o un aviso."""
        employee = self._directory.get_employee(employee_id)
        if employee is None:
            return f"No existe un empleado con id {employee_id}"
        return json.dumps(employee, ensure_ascii=False)

    def _search_by_department(self, department: str) -> str:
        """Herramienta ``search_by_department``: la lista en JSON o un aviso."""
        people = self._directory.search_by_department(department)
        if not people:
            return f"No hay empleados en el departamento {department}"
        return json.dumps(people, ensure_ascii=False)
