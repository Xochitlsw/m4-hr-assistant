"""Directorio de empleados de solo lectura sobre ``data/employees.json``."""

import json
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from app.config import ConfigError


def normalize(text: str) -> str:
    """Pasa el texto a minúsculas y le quita los acentos, para comparar.

    :param text: Texto original
    :type text: str
    :return: Texto normalizado
    :rtype: str
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


class EmployeeDirectory:
    """Consulta de empleados en memoria; nunca modifica los datos.

    :param employees: Registros con al menos ``id`` y ``department``
    :type employees: Iterable[dict]
    """

    def __init__(self, employees: Iterable[dict]):
        self._employees = tuple(dict(employee) for employee in employees)
        self._by_id = {employee["id"]: employee for employee in self._employees}

    @classmethod
    def load(cls, path: Path) -> "EmployeeDirectory":
        """Carga el directorio desde un archivo JSON.

        :param path: Ruta del archivo con la lista de empleados
        :type path: pathlib.Path
        :return: El directorio cargado
        :rtype: EmployeeDirectory
        :raises ConfigError: Si el archivo no existe o no es una lista válida
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(data)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigError(
                f"EMPLOYEES_PATH must be a readable JSON list of employees: {path}"
            ) from error

    def get_employee(self, employee_id: str) -> dict | None:
        """Busca un empleado por su id (por ejemplo ``EMP-0004``).

        :param employee_id: Id del empleado
        :type employee_id: str
        :return: Una copia del registro, o ``None`` si no existe
        :rtype: dict | None
        """
        employee = self._by_id.get(employee_id.strip().upper())
        return dict(employee) if employee else None

    def search_by_department(self, department: str) -> list[dict]:
        """Lista los empleados de un departamento, sin distinguir mayúsculas ni acentos.

        :param department: Nombre del departamento
        :type department: str
        :return: Copias de los registros, en el orden de los datos
        :rtype: list[dict]
        """
        wanted = normalize(department.strip())
        return [
            dict(employee)
            for employee in self._employees
            if normalize(employee["department"]) == wanted
        ]

    def departments(self) -> list[str]:
        """Nombres de los departamentos, sin repetir y en el orden de los datos.

        :return: Nombres tal como aparecen en los datos
        :rtype: list[str]
        """
        names = (employee["department"] for employee in self._employees)
        return list(dict.fromkeys(names))
