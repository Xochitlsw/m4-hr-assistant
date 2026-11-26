"""Exporta el contrato OpenAPI de la API a ``doc/openapi.json`` (ADR 0019).

Uso, desde ``product/``: ``uv run python scripts/export_openapi.py``.

Arma la aplicación con la configuración por omisión (modelo simulado, sin claves ni red)
y escribe el esquema en UTF-8 con Python: la redirección ``>`` de PowerShell 5.1 puede
escribir UTF-16. ``tests/test_api_contract.py`` falla si el archivo y la API no
coinciden, así que hay que correr este script después de cambiar la API.
"""

import json
import sys
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
DOC = PRODUCT / "doc" / "openapi.json"


def export() -> Path:
    """Escribe ``doc/openapi.json`` con el esquema de la aplicación.

    :return: La ruta escrita
    :rtype: pathlib.Path
    """
    sys.path.insert(0, str(PRODUCT))
    # La ruta del producto se agrega justo arriba: pylint no la ve.
    # pylint: disable=import-outside-toplevel,import-error
    from app.config import load_settings
    from app.main import create_app

    schema = create_app(settings=load_settings({})).openapi()
    DOC.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return DOC


if __name__ == "__main__":
    print(f"Escrito {export().relative_to(PRODUCT).as_posix()}")
