"""Pruebas de las métricas del proceso: percentiles, errores, tokens y SLO."""

import os

from app.metrics import Metrics


def test_percentiles_use_the_nearest_rank():
    metrics = Metrics(slo_p95_ms=2000)
    for latency in (0.798, 0.88):
        metrics.record_request("/ask", 200, latency)
    assert metrics.snapshot()["latency_ms"] == {"p50": 0.798, "p95": 0.88}
    twenty = Metrics(slo_p95_ms=2000)
    for latency in range(20, 0, -1):
        twenty.record_request("/ask", 200, float(latency))
    snapshot = twenty.snapshot()
    assert snapshot["latency_ms"] == {"p50": 10.0, "p95": 19.0}
    assert snapshot["pid"] == os.getpid()


def test_error_rate_counts_only_server_errors():
    metrics = Metrics(slo_p95_ms=2000)
    for status in (200, 422, 500, 200):
        metrics.record_request("/ask", status, 1.0)
    snapshot = metrics.snapshot()
    assert snapshot["requests"] == 4
    assert snapshot["error_rate"] == 0.25


def test_health_and_metrics_requests_are_not_counted():
    metrics = Metrics(slo_p95_ms=2000)
    metrics.record_request("/health", 200, 1.0)
    metrics.record_request("/metrics", 200, 1.0)
    snapshot = metrics.snapshot()
    assert snapshot["requests"] == 0
    assert snapshot["error_rate"] == 0.0
    assert snapshot["latency_ms"] == {"p50": 0.0, "p95": 0.0}
    assert snapshot["slo"] == {"p95_ms_target": 2000, "met": True}


def test_the_slo_fails_when_p95_reaches_the_target():
    metrics = Metrics(slo_p95_ms=100)
    for latency in (50.0, 100.0):
        metrics.record_request("/ask", 200, latency)
    assert metrics.snapshot()["slo"] == {"p95_ms_target": 100, "met": False}


def test_only_the_latest_latencies_and_all_tokens_are_kept():
    metrics = Metrics(slo_p95_ms=2000, window=3)
    for latency in (900.0, 1.0, 2.0, 3.0):
        metrics.record_request("/ask", 200, latency)
    metrics.add_tokens(10, 4)
    metrics.add_tokens(5, 1)
    snapshot = metrics.snapshot()
    assert snapshot["requests"] == 4
    assert snapshot["latency_ms"]["p95"] == 3.0
    assert snapshot["tokens"] == {"input": 15, "output": 5}


def test_the_cost_adds_up():
    metrics = Metrics(slo_p95_ms=2000)
    assert metrics.snapshot()["cost_usd"] == 0
    metrics.add_cost(0.0000834)
    metrics.add_cost(0.0001)
    assert metrics.snapshot()["cost_usd"] == 0.0001834
