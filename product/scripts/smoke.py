"""Prueba de humo del HR Assistant publicado.

Uso (la clave va en una variable de entorno, nunca en el comando ni en un chat):

    SMOKE_API_KEY=<clave> python scripts/smoke.py https://<servicio>.onrender.com

Comprueba tres cosas e imprime ``[OK]`` o ``[FAIL]`` en cada una, con el código HTTP
recibido (401: la clave no coincide; 502: el modelo no responde):

- ``GET /health`` responde 200.
- ``POST /ask`` sin clave responde 401.
- ``POST /ask`` con clave responde 200.

Termina con código 0 solo si las tres pasan.
"""

import os
import sys

import httpx

QUESTION = {"question": "¿Quién es EMP-0004?"}
# Render despierta un servicio dormido en hasta un minuto.
TIMEOUT_SECONDS = 90


def run_checks(
    url: str, key: str, client: httpx.Client
) -> list[tuple[str, int, int]]:
    """Hace las tres peticiones de la prueba de humo.

    :param url: URL base del servicio, sin ``/`` al final
    :type url: str
    :param key: Clave de un cliente de ``API_KEYS``
    :type key: str
    :param client: Cliente HTTP
    :type client: httpx.Client
    :return: ``(nombre, código esperado, código recibido)`` por cada comprobación
    :rtype: list[tuple[str, int, int]]
    """
    health = client.get(f"{url}/health")
    without_key = client.post(f"{url}/ask", json=QUESTION)
    with_key = client.post(f"{url}/ask", json=QUESTION, headers={"X-API-Key": key})
    return [
        ("GET /health", 200, health.status_code),
        ("POST /ask sin clave", 401, without_key.status_code),
        ("POST /ask con clave", 200, with_key.status_code),
    ]


def main(args: list[str]) -> int:
    """Corre la prueba de humo e imprime el resultado de cada comprobación.

    :param args: Argumentos del comando: solo la URL del servicio
    :type args: list[str]
    :return: 0 si todo pasa, 1 si algo falla, 2 si falta la URL o la clave
    :rtype: int
    """
    key = os.environ.get("SMOKE_API_KEY", "").strip()
    if len(args) != 1 or not key:
        print("Uso: SMOKE_API_KEY=<clave> python scripts/smoke.py <url>")
        return 2
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        results = run_checks(args[0].rstrip("/"), key, client)
    for name, expected, received in results:
        status = "OK" if received == expected else "FAIL"
        print(f"  [{status}] {name} -> {received} (esperado {expected})")
    return 0 if all(expected == received for _, expected, received in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
