"""Agente del HR Assistant: bucle con tope sobre el protocolo JSON del modelo.

En cada paso el modelo pide una herramienta (``{"tool", "args"}``) o responde
(``{"answer"}``). Solo se ejecutan las herramientas de la lista blanca, con sus
argumentos validados y normalizados (ADR 0013): ``employee_id`` con la forma
``EMP-0000`` y ``department`` entre los departamentos de los datos. Una respuesta
fuera del protocolo consume un paso, se le devuelve al modelo como error (sin repetir
lo que escribió) y se avisa con su motivo en ``on_invalid_reply``.

La corrida, cada llamada al modelo y cada herramienta quedan como trazas cuando el
contexto de la petición las enciende (``app.tracing``). El agente no sabe de logs ni
de ``request_id``: avisa de cada herramienta ejecutada con ``on_tool_call``.
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.directory import EmployeeDirectory, normalize
from app.llm import TOOL_RESULT_PREFIX, ChatModel, Completion
from app.tracing import traced

ToolCallback = Callable[[str, dict], None]
InvalidReplyCallback = Callable[[str], None]
# [0-9] y no \d: en Python \d acepta dígitos de cualquier alfabeto.
EMPLOYEE_ID_FORMAT = re.compile(r"EMP-[0-9]{4}")

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
class Usage:
    """Consumo sumado de todas las llamadas al modelo de una pregunta.

    :param input_tokens: Tokens de entrada
    :type input_tokens: int
    :param output_tokens: Tokens de salida
    :type output_tokens: int
    :param cached_calls: Llamadas que contestó la caché del gateway
    :type cached_calls: int
    :param cost_usd: Costo en USD (las cacheadas no cuestan)
    :type cost_usd: float
    :param retries: Reintentos que hicieron falta en todas las llamadas
    :type retries: int
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_calls: int = 0
    cost_usd: float = 0.0
    retries: int = 0

    def add(self, completion: Completion) -> "Usage":
        """Suma una llamada al modelo.

        :param completion: Respuesta de una llamada
        :type completion: app.llm.Completion
        :return: El consumo con esa llamada incluida
        :rtype: Usage
        """
        return Usage(
            input_tokens=self.input_tokens + completion.input_tokens,
            output_tokens=self.output_tokens + completion.output_tokens,
            cached_calls=self.cached_calls + int(completion.cached),
            cost_usd=self.cost_usd + completion.cost_usd,
            retries=self.retries + completion.attempts - 1,
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
    :param stopped: Por qué terminó: ``answer`` (respondió) o ``max_steps`` (tope)
    :type stopped: str
    :param usage: Tokens, llamadas cacheadas y costo de todos los pasos
    :type usage: Usage
    """

    answer: str
    steps: int
    tools_used: list[str]
    stopped: str
    usage: Usage

    @property
    def input_tokens(self) -> int:
        """Tokens de entrada sumados de todos los pasos."""
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        """Tokens de salida sumados de todos los pasos."""
        return self.usage.output_tokens


class InvalidReply(ValueError):
    """El modelo respondió algo fuera del protocolo.

    :param reason: Motivo corto para la auditoría (``not_json``, ``bad_shape``,
        ``empty_answer``, ``unknown_tool``, ``bad_args``, ``bad_employee_id`` o
        ``unknown_department``)
    :type reason: str
    :param message: Explicación para el modelo, sin repetir lo que escribió
    :type message: str
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


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
        self._directory = directory
        self._max_steps = max_steps
        self._departments = {normalize(name): name for name in directory.departments()}
        self._complete = traced("llm", "model", model.complete)
        # nombre -> (argumento, validador que normaliza, herramienta)
        self._tools = {
            "get_employee": (
                "employee_id",
                self._valid_employee_id,
                traced("tool", "get_employee", self._get_employee),
            ),
            "search_by_department": (
                "department",
                self._valid_department,
                traced("tool", "search_by_department", self._search_by_department),
            ),
        }
        self._traced_run = traced("chain", "agent_run", self._run)

    def run(
        self,
        question: str,
        history: Sequence[dict] = (),
        on_tool_call: ToolCallback | None = None,
        on_invalid_reply: InvalidReplyCallback | None = None,
    ) -> AgentResult:
        """Responde una pregunta con el historial que manda el cliente.

        :param question: Pregunta de la persona
        :type question: str
        :param history: Turnos previos con ``role`` y ``content``
        :type history: Sequence[dict]
        :param on_tool_call: Se llama con ``(herramienta, argumentos normalizados)`` por
            cada herramienta ejecutada (las rechazadas no cuentan)
        :type on_tool_call: Callable[[str, dict], None] | None
        :param on_invalid_reply: Se llama con el motivo de cada respuesta del modelo
            fuera del protocolo (por ejemplo ``bad_employee_id``), nunca con su texto
        :type on_invalid_reply: Callable[[str], None] | None
        :return: Respuesta, pasos, herramientas usadas, tokens y motivo de parada
        :rtype: AgentResult
        """
        callbacks = (on_tool_call, on_invalid_reply)
        return self._traced_run(question, list(history), callbacks)

    def _run(
        self,
        question: str,
        history: list[dict],
        callbacks: tuple[ToolCallback | None, InvalidReplyCallback | None],
    ) -> AgentResult:
        """Bucle con tope; ver :meth:`run`."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [
            {"role": turn["role"], "content": turn["content"]} for turn in history
        ]
        messages.append({"role": "user", "content": question})
        tools_used: list[str] = []
        usage = Usage()
        for step in range(1, self._max_steps + 1):
            completion = self._complete(messages)
            usage = usage.add(completion)
            answer, tool_result = self._handle(completion.text, tools_used, callbacks)
            if answer is not None:
                return AgentResult(answer, step, tools_used, "answer", usage)
            messages.append({"role": "assistant", "content": completion.text})
            messages.append(
                {"role": "user", "content": TOOL_RESULT_PREFIX + tool_result}
            )
        return AgentResult(CAP_REACHED, self._max_steps, tools_used, "max_steps", usage)

    def _handle(
        self,
        text: str,
        tools_used: list[str],
        callbacks: tuple[ToolCallback | None, InvalidReplyCallback | None],
    ) -> tuple[str | None, str]:
        """Interpreta una respuesta del modelo.

        :return: ``(respuesta, "")`` si el modelo respondió, o ``(None, resultado)``
            con el resultado de la herramienta o el error del protocolo
        """
        on_tool_call, on_invalid_reply = callbacks
        try:
            reply = self._parse(text)
        except InvalidReply as error:
            if on_invalid_reply is not None:
                on_invalid_reply(error.reason)
            return None, f"error: {error}"
        if "answer" in reply:
            return reply["answer"], ""
        name, args = reply["tool"], reply["args"]
        argument_name, _, tool = self._tools[name]
        tools_used.append(name)
        if on_tool_call is not None:
            on_tool_call(name, dict(args))
        return None, tool(args[argument_name])

    def _parse(self, text: str) -> dict:
        """Valida el protocolo, la lista blanca y los argumentos.

        :return: ``{"answer": ...}`` o ``{"tool": ..., "args": ...}`` con los
            argumentos ya normalizados
        :raises InvalidReply: Si algo no cumple; el mensaje no repite lo que escribió
            el modelo
        """
        try:
            reply = json.loads(text)
        except ValueError as error:
            raise InvalidReply("not_json", "la respuesta no es JSON válido") from error
        if isinstance(reply, dict) and set(reply) == {"answer"}:
            if isinstance(reply["answer"], str):
                if not reply["answer"].strip():
                    raise InvalidReply("empty_answer", "la respuesta está vacía")
                return reply
        if not isinstance(reply, dict) or set(reply) != {"tool", "args"}:
            raise InvalidReply(
                "bad_shape", 'responde solo {"tool", "args"} o {"answer"}'
            )
        name, args = reply["tool"], reply["args"]
        if not isinstance(name, str) or name not in self._tools:
            known = ", ".join(self._tools)
            raise InvalidReply("unknown_tool", f"herramienta desconocida; usa: {known}")
        argument_name, validate, _ = self._tools[name]
        if not isinstance(args, dict) or set(args) != {argument_name}:
            raise InvalidReply(
                "bad_args", f'{name} recibe solo {{"{argument_name}": "..."}}'
            )
        if not isinstance(args[argument_name], str):
            raise InvalidReply("bad_args", f"{argument_name} debe ser texto")
        return {"tool": name, "args": {argument_name: validate(args[argument_name])}}

    @staticmethod
    def _valid_employee_id(value: str) -> str:
        """``employee_id`` normalizado (``EMP-0004``) o :class:`InvalidReply`."""
        employee_id = value.strip().upper()
        if not EMPLOYEE_ID_FORMAT.fullmatch(employee_id):
            raise InvalidReply(
                "bad_employee_id", "employee_id debe tener la forma EMP-0000"
            )
        return employee_id

    def _valid_department(self, value: str) -> str:
        """Nombre del departamento tal como está en los datos, o :class:`InvalidReply`.

        Se compara sin mayúsculas ni acentos. El error lista los departamentos que
        existen, sin repetir el texto que mandó el modelo.
        """
        department = self._departments.get(normalize(value.strip()))
        if department is None:
            known = ", ".join(self._departments.values())
            raise InvalidReply(
                "unknown_department", f"departamento desconocido; usa uno de: {known}"
            )
        return department

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
