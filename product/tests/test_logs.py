"""Pruebas del log de eventos: una línea JSON por evento, segura entre hilos."""

import io
import json
import threading
from datetime import datetime, timezone

from app.logs import EventLogger


class StoppedClock:
    """Reloj detenido en una hora conocida."""

    def now(self):
        return datetime(2026, 11, 25, 12, 30, 15, tzinfo=timezone.utc)

    def monotonic(self):
        return 0.0


def test_each_event_is_one_json_line_with_the_common_fields():
    stream = io.StringIO()
    logger = EventLogger("hr-assistant", StoppedClock(), stream)
    logger.emit("audit", "demo-123", tool="search_by_department", args={"x": "Díaz"})
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert list(record)[:4] == ["ts", "service", "event", "request_id"]
    assert record["ts"] == "2026-11-25T12:30:15+00:00"
    assert (record["service"], record["event"]) == ("hr-assistant", "audit")
    assert record["args"] == {"x": "Díaz"}


def test_lines_written_by_many_threads_never_mix():
    stream = io.StringIO()
    logger = EventLogger("hr-assistant", StoppedClock(), stream)

    def write_many(name):
        for number in range(50):
            logger.emit("request", f"{name}-{number}", path="/ask", status=200)

    threads = [threading.Thread(target=write_many, args=(f"t{n}",)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(records) == 400
    assert len({record["request_id"] for record in records}) == 400
