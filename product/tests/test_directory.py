"""Pruebas del directorio de empleados (solo lectura)."""


def test_get_employee_returns_the_employee_or_none(directory):
    assert directory.get_employee("EMP-0004")["name"] == "Jorge Díaz"
    assert directory.get_employee("EMP-9999") is None


def test_search_by_department_ignores_case_and_accents(directory):
    people = directory.search_by_department("tecnologia")
    assert [person["name"] for person in people] == [
        "Ana López",
        "Marta Ruiz",
        "Jorge Díaz",
    ]
    assert directory.search_by_department("Marketing") == []
