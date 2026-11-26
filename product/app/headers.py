"""Cabeceras de seguridad en cada respuesta (ADR 0016).

Todas las respuestas llevan ``nosniff``, ``X-Frame-Options: DENY``, ``Referrer-Policy:
no-referrer``, aislamiento de origen, una ``Permissions-Policy`` que apaga cámara,
micrófono, ubicación y pagos, y ``Cache-Control: no-store`` (nada de la API se guarda en
cachés ni en el navegador). La ``Content-Security-Policy`` depende de la ruta:

- ``/``: solo el ``<script>`` y el ``<style>`` en línea de la página, por su hash
  SHA-256; ``fetch`` solo al mismo origen. Si alguien inyectara un script, no correría.
- ``/docs``: Swagger UI se carga de cdn.jsdelivr.net y su arranque es un script en línea
  que arma FastAPI; esa página no tiene datos de personas.
- Lo demás (JSON): ``default-src 'none'``.

En producción se agrega HSTS: Render sirve por HTTPS y el navegador no vuelve a usar
HTTP. CORS queda cerrado: la API no manda ``Access-Control-Allow-Origin`` y un navegador
de otro origen no puede leer sus respuestas.
"""

import base64
import hashlib
import re
from collections.abc import Callable

from fastapi import FastAPI, Request

COMMON = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    ),
    "Cache-Control": "no-store",
}
API_POLICY = "default-src 'none'; frame-ancestors 'none'"
DOCS_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' data: https://fastapi.tiangolo.com",
        "connect-src 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ]
)
DOCS_PATHS = ("/docs", "/docs/oauth2-redirect")
HSTS = "max-age=31536000; includeSubDomains"
INLINE = re.compile(r"<(script|style)>(.*?)</\1>", re.DOTALL)


def inline_hashes(html: str, tag: str) -> list[str]:
    """Hashes CSP (``'sha256-...'``) de cada bloque ``<tag>`` en línea del HTML.

    :param html: Página completa
    :type html: str
    :param tag: ``script`` o ``style``
    :type tag: str
    :return: Un hash por bloque, en orden
    :rtype: list[str]
    """
    hashes = []
    for found, content in INLINE.findall(html):
        if found == tag:
            digest = hashlib.sha256(content.encode("utf-8")).digest()
            hashes.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return hashes


def page_policy(html: str) -> str:
    """CSP de la página: solo su script y su estilo en línea; ``fetch`` al mismo origen.

    :param html: Página completa
    :type html: str
    :return: El valor de ``Content-Security-Policy``
    :rtype: str
    """
    return "; ".join(
        [
            "default-src 'none'",
            "script-src " + " ".join(inline_hashes(html, "script")),
            "style-src " + " ".join(inline_hashes(html, "style")),
            "connect-src 'self'",
            "img-src 'self'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def headers_for(path: str, page: str, production: bool) -> dict[str, str]:
    """Cabeceras de seguridad para una ruta.

    :param path: Ruta de la petición
    :type path: str
    :param page: CSP de la página (:func:`page_policy`)
    :type page: str
    :param production: ``True`` con ``ENVIRONMENT=production`` (agrega HSTS)
    :type production: bool
    :return: Cabeceras a poner en la respuesta
    :rtype: dict[str, str]
    """
    if path == "/":
        policy = page
    elif path in DOCS_PATHS:
        policy = DOCS_POLICY
    else:
        policy = API_POLICY
    headers = {**COMMON, "Content-Security-Policy": policy}
    if production:
        headers["Strict-Transport-Security"] = HSTS
    return headers


def add_security_headers(app: FastAPI, html: str, production: bool) -> None:
    """Middleware que pone las cabeceras en toda respuesta, también en los errores.

    Se agrega al final para quedar por fuera de todos los demás.

    :param app: La aplicación
    :type app: fastapi.FastAPI
    :param html: Página servida en ``/``
    :type html: str
    :param production: ``True`` con ``ENVIRONMENT=production``
    :type production: bool
    """
    page = page_policy(html)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable):
        """Pone las cabeceras de la ruta en la respuesta."""
        response = await call_next(request)
        response.headers.update(headers_for(request.url.path, page, production))
        return response
