"""Contrato de la API en OpenAPI: seguridad, errores, ejemplos y el archivo exportado.

``doc/openapi.json`` se genera con ``scripts/export_openapi.py``. Si alguien cambia la
API sin regenerarlo, esta prueba falla (ADR 0019).
"""

import json
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "doc" / "openapi.json"
ASK_ERRORS = {"400", "401", "413", "422", "429", "502", "503"}


def schema(client):
    return client.get("/openapi.json").json()


def test_the_api_key_is_a_declared_security_scheme(client):
    schemes = schema(client)["components"]["securitySchemes"]
    assert schemes["APIKeyHeader"] == {
        "type": "apiKey",
        "description": "API key of the client",
        "in": "header",
        "name": "X-API-Key",
    }


def test_ask_documents_every_error_with_its_body(client):
    responses = schema(client)["paths"]["/ask"]["post"]["responses"]
    assert ASK_ERRORS <= set(responses)
    for code in ASK_ERRORS - {"422"}:
        content = responses[code]["content"]["application/json"]
        assert content["schema"] == {"$ref": "#/components/schemas/ErrorBody"}
        assert "detail" in content["example"]
    for code in ("429", "503"):
        assert "Retry-After" in responses[code]["headers"]


def test_bodies_carry_examples_and_operations_have_tags(client):
    document = schema(client)
    components = document["components"]["schemas"]
    for name in ("AskRequest", "AskResponse", "HealthResponse", "ReadyResponse"):
        assert components[name].get("examples"), name
    tags = {tag["name"] for tag in document["tags"]}
    assert {"ask", "operations", "page"} <= tags
    for path, methods in document["paths"].items():
        for operation in methods.values():
            assert operation["tags"], path
            assert operation["description"], path


def test_health_and_ready_have_response_models(client):
    paths = schema(client)["paths"]
    for path, model in (("/health", "HealthResponse"), ("/ready", "ReadyResponse")):
        ok = paths[path]["get"]["responses"]["200"]["content"]["application/json"]
        assert ok["schema"] == {"$ref": f"#/components/schemas/{model}"}


def test_the_exported_document_matches_the_running_api(client):
    assert json.loads(DOC.read_text(encoding="utf-8")) == schema(client)
