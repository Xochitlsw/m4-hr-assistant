"""Prueba de carga del HR Assistant: peticiones concurrentes, códigos y latencias.

Uso: ``uv run python scripts/load_test.py <url> [--requests N] [--concurrency C]``, con
la clave de un cliente en ``SMOKE_API_KEY``, leída sin eco (README, «Despliegue»).

Manda ``N`` preguntas a ``POST /ask`` con a lo más ``C`` a la vez, con un solo
``httpx.AsyncClient`` para todas: crear un cliente por petición mediría al cliente y no
al servidor. Imprime el total por código (``200``, ``429``...), los errores de red, las
peticiones por segundo y la latencia p50, p95 y máxima.

En local, con el modelo simulado y ``RATE_LIMIT_PER_MINUTE`` alto, mide la concurrencia;
con el límite por omisión, muestra que el ``429`` protege al servicio. Contra Render usa
el modelo real: pocas peticiones (por ejemplo 20 con 5 a la vez).
"""

import argparse
import asyncio
import math
import os
import sys
import time
from collections import Counter

import httpx

QUESTION = {"question": "¿Quién es EMP-0004?"}
TIMEOUT_SECONDS = 90


def percentile(values: list[float], percent: float) -> float:
    """Percentil por rango más cercano (0 si no hay valores).

    :param values: Latencias en milisegundos
    :type values: list[float]
    :param percent: Percentil entre 0 y 100
    :type percent: float
    :return: El valor del percentil
    :rtype: float
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(math.ceil(percent / 100 * len(ordered)), 1) - 1]


async def run_load(
    client: httpx.AsyncClient, key: str, requests: int, concurrency: int
) -> dict:
    """Manda ``requests`` preguntas con a lo más ``concurrency`` a la vez.

    :param client: Cliente asíncrono con la URL base del servicio
    :type client: httpx.AsyncClient
    :param key: Clave de un cliente
    :type key: str
    :param requests: Peticiones en total
    :type requests: int
    :param concurrency: Peticiones a la vez
    :type concurrency: int
    :return: Resumen con conteos por código, errores de red, ritmo y latencias
    :rtype: dict
    """
    gate = asyncio.Semaphore(concurrency)
    statuses: Counter = Counter()
    latencies: list[float] = []
    errors = 0

    async def one() -> None:
        nonlocal errors
        async with gate:
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/ask", json=QUESTION, headers={"X-API-Key": key}
                )
            except httpx.HTTPError:
                errors += 1
                return
            latencies.append((time.perf_counter() - started) * 1000)
            statuses[response.status_code] += 1

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(requests)))
    seconds = time.perf_counter() - started
    return {
        "requests": requests,
        "concurrency": concurrency,
        "seconds": round(seconds, 3),
        "requests_per_second": round(requests / seconds, 1) if seconds else 0.0,
        "by_status": dict(sorted(statuses.items())),
        "network_errors": errors,
        "p50_ms": round(percentile(latencies, 50), 1),
        "p95_ms": round(percentile(latencies, 95), 1),
        "max_ms": round(max(latencies, default=0.0), 1),
    }


def format_report(summary: dict) -> str:
    """Resumen en texto, una línea por dato.

    :param summary: Resultado de :func:`run_load`
    :type summary: dict
    :return: El informe
    :rtype: str
    """
    by_status = summary["by_status"].items()
    codes = ", ".join(f"{code}: {count}" for code, count in by_status)
    return "\n".join(
        [
            f"Peticiones: {summary['requests']} con {summary['concurrency']} a la vez "
            f"en {summary['seconds']} s ({summary['requests_per_second']} por segundo)",
            f"Por código: {codes}",
            f"Errores de red: {summary['network_errors']}",
            f"Latencia: p50 {summary['p50_ms']} ms, p95 {summary['p95_ms']} ms, "
            f"máxima {summary['max_ms']} ms",
        ]
    )


async def _main(url: str, key: str, requests: int, concurrency: int) -> dict:
    async with httpx.AsyncClient(base_url=url, timeout=TIMEOUT_SECONDS) as client:
        return await run_load(client, key, requests, concurrency)


def main(args: list[str]) -> int:
    """Corre la prueba e imprime el informe.

    :param args: URL y, opcionales, ``--requests`` y ``--concurrency``
    :type args: list[str]
    :return: 0 si no hubo errores de red ni ``5xx``; 1 si los hubo; 2 si falta la clave
    :rtype: int
    """
    parser = argparse.ArgumentParser(description="Prueba de carga del HR Assistant")
    parser.add_argument("url")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    options = parser.parse_args(args)
    key = os.environ.get("SMOKE_API_KEY", "").strip()
    if not key:
        print("Falta la clave en la variable SMOKE_API_KEY (leela sin eco).")
        return 2
    summary = asyncio.run(
        _main(options.url.rstrip("/"), key, options.requests, options.concurrency)
    )
    print(format_report(summary))
    failures = sum(n for code, n in summary["by_status"].items() if code >= 500)
    return 1 if failures or summary["network_errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
