"""Request metrics and structured access logs (Phase 2: production-readiness).

GET /metrics returns Prometheus text format so a standard scraper (Prometheus, Grafana Agent,
Render/Datadog integrations) can collect it. Routes are grouped by their template (e.g.
/employer/{employer_id}/attendance), so IDs never become label values. Kept in memory per process:
enough for one instance; a multi-instance deployment would use a Prometheus client with a push or
multiprocess collector.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

log = logging.getLogger("salarybridge.access")
WINDOW = 1_000  # latency samples kept per route for quantiles


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = time.time()
        self.count: dict[tuple[str, str, int], int] = defaultdict(int)
        self.latency: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=WINDOW))
        self.decisions: dict[str, int] = defaultdict(int)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self.lock:
            self.count[(method, route, status)] += 1
            self.latency[(method, route)].append(seconds)

    def decision(self, status: str) -> None:
        with self.lock:
            self.decisions[status] += 1

    def render(self) -> str:
        lines = [
            "# HELP salarybridge_uptime_seconds Seconds since this process started.",
            "# TYPE salarybridge_uptime_seconds gauge",
            f"salarybridge_uptime_seconds {time.time() - self.started:.0f}",
            "# HELP salarybridge_http_requests_total HTTP requests by method, route template and status.",
            "# TYPE salarybridge_http_requests_total counter",
        ]
        with self.lock:
            for (m, r, s), n in sorted(self.count.items()):
                lines.append(f'salarybridge_http_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}')
            lines += [
                "# HELP salarybridge_http_latency_seconds Request latency quantiles over the last 1000 requests per route.",
                "# TYPE salarybridge_http_latency_seconds summary",
            ]
            for (m, r), samples in sorted(self.latency.items()):
                ordered = sorted(samples)
                for q in (0.5, 0.95, 0.99):
                    value = ordered[min(len(ordered) - 1, int(q * len(ordered)))]
                    lines.append(f'salarybridge_http_latency_seconds{{method="{m}",route="{r}",quantile="{q}"}} {value:.4f}')
                lines.append(f'salarybridge_http_latency_seconds_count{{method="{m}",route="{r}"}} {len(ordered)}')
            lines += [
                "# HELP salarybridge_decisions_total Advance decisions by outcome.",
                "# TYPE salarybridge_decisions_total counter",
            ]
            for status, n in sorted(self.decisions.items()):
                lines.append(f'salarybridge_decisions_total{{status="{status}"}} {n}')
        return "\n".join(lines) + "\n"


METRICS = Metrics()


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _observe(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = getattr(request.scope.get("route"), "path", "unmatched")
            METRICS.observe(request.method, route, status, elapsed)
            # One JSON line per request: easy to ship to any log store. No bodies, no personal data.
            log.info(json.dumps({"request_id": request_id, "method": request.method, "route": route, "status": status, "ms": round(elapsed * 1000, 1)}))

    @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    def metrics() -> str:
        return METRICS.render()
