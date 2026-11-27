"""Detector de datos personales y secretos (ADR 0014).

Todos los valores son sintéticos. Los que tienen forma de secreto se arman por partes
para que ningún escáner de secretos los confunda con uno real.
"""

import json
from pathlib import Path

import pytest

from app.privacy import redact_tree, scan
from app.sanitize import detection_view

EMPLOYEES = Path(__file__).resolve().parents[1] / "data" / "employees.json"

ZERO_WIDTH_SPACE = chr(0x200B)
SOFT_HYPHEN = chr(0x00AD)
CURP = "XAXX010101HDFXXX09"
RFC = "XAXX010101000"
EMAIL = "persona.prueba@example.com"
CARD = "4111 1111 1111 1111"
CLABE = "002180000000000001"
NSS = "12345678901"
SECRETS = {
    "PRIVATE_KEY": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "JWT": ".".join(
        ["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJ0ZXN0In0", "c2ln" * 4]
    ),
    "AWS_KEY": "AKIA" + "Z" * 16,
    "GITHUB_TOKEN": "ghp" + "_" + "a1" * 18,
    "LANGSMITH_KEY": "lsv2" + "_pt_" + "b2" * 16,
    "BEARER": "Bearer " + "c3d4" * 6,
    "CREDENTIAL": "contraseña" + ": " + "e5f6" * 3,
}


@pytest.mark.parametrize(
    "kind, value",
    [
        ("CURP", CURP),
        ("CURP", CURP.lower()),
        ("RFC", RFC),
        ("EMAIL", EMAIL),
        ("CARD", CARD),
        ("CLABE", CLABE),
        ("NSS", NSS),
        ("PHONE", "55 1234 5678"),
        ("PHONE", "+52 55 1234 5678"),
        ("PHONE", "5512345678"),
    ],
)
def test_personal_data_is_found_and_redacted(kind, value):
    found = scan(f"Mis datos: {value}, gracias.")
    assert found.pii == {kind: 1}
    assert not found.secrets
    assert found.text == f"Mis datos: [REDACTED:{kind}], gracias."


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_secrets_are_found_and_redacted(kind):
    found = scan(f"Usa esto: {SECRETS[kind]} por favor")
    assert found.secrets == {kind: 1}
    assert SECRETS[kind] not in found.text
    assert f"[REDACTED:{kind}]" in found.text


@pytest.mark.parametrize("kind", ["pt", "sk", "tr"])
def test_every_langsmith_key_kind_is_a_secret(kind):
    """Personal, de servicio y Tracing Only (``lsv2_tr_``, la de mínimo privilegio)."""
    key = "lsv2" + f"_{kind}_" + "d4" * 16
    assert scan(f"mi clave {key}").secrets == {"LANGSMITH_KEY": 1}


def test_a_ten_digit_folio_is_a_known_false_positive():
    """Límite documentado en el ADR 0014: 10 dígitos se leen como un teléfono."""
    found = scan("El folio de mi trámite es 1234567890")
    assert found.text == "El folio de mi trámite es [REDACTED:PHONE]"


def test_a_number_that_fails_luhn_is_not_a_card():
    assert "CARD" not in scan("Folio 4111 1111 1111 1112").pii


@pytest.mark.parametrize(
    "text",
    [
        "¿Quién es EMP-0004?",
        "¿Quiénes trabajan en Finanzas?",
        "Ingresó el 2021-05-17 y el 2019-03-04; tiene 3 hijos y gana $5,000.",
        "La clave del curso es la 12 y el token de acceso se pide en RH.",
    ],
)
def test_ordinary_hr_text_is_left_alone(text):
    found = scan(text)
    assert (found.text, found.pii, found.secrets) == (text, {}, {})


def test_directory_records_are_never_redacted():
    """Nombre, puesto, departamento, fecha de ingreso y estado son el propósito."""
    employees = json.loads(EMPLOYEES.read_text(encoding="utf-8"))
    for employee in employees:
        record = json.dumps(employee, ensure_ascii=False)
        assert not scan(record).found, employee["id"]


def test_invisible_characters_and_lookalike_digits_do_not_hide_data():
    hidden_email = (
        "persona" + ZERO_WIDTH_SPACE + ".prueba@exam" + SOFT_HYPHEN + "ple.com"
    )
    wide_phone = "".join(chr(0xFF10 + int(digit)) for digit in "5512345678")
    arabic_nss = "".join(chr(0x0660 + int(digit)) for digit in NSS)
    for value, kind in ((hidden_email, "EMAIL"), (wide_phone, "PHONE")):
        assert scan(f"dato {value}").pii == {kind: 1}
    assert scan(f"dato {arabic_nss}").pii == {"NSS": 1}


def test_the_detection_view_drops_format_characters_and_unifies_digits():
    text = "ig" + ZERO_WIDTH_SPACE + "nora " + chr(0xFF15) + chr(0x0663)
    assert detection_view(text) == "ignora 53"


def test_redacting_twice_finds_nothing_new():
    once = scan(f"{CURP} {EMAIL} {SECRETS['JWT']}")
    again = scan(once.text)
    assert (again.text, again.found) == (once.text, False)


def test_a_tree_is_redacted_string_by_string():
    tree = {
        "inputs": {"question": f"Mi correo es {EMAIL}", "steps": 2},
        "outputs": [{"answer": f"CURP {CURP}"}, None, True, 3.5],
    }
    redacted = redact_tree(tree)
    assert redacted == {
        "inputs": {"question": "Mi correo es [REDACTED:EMAIL]", "steps": 2},
        "outputs": [{"answer": "CURP [REDACTED:CURP]"}, None, True, 3.5],
    }
