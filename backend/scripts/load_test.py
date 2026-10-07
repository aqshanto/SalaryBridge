"""Small load test: concurrent workers hit the API, each in its own sandbox session.

Usage (API running):  .venv/Scripts/python.exe -m scripts.load_test --base http://localhost:8000 --workers 10 --seconds 60
Mix per loop: health check, an advance decision (full rules + M1/M2/M3/M5 + SHAP), and the ops summary.
Prints throughput, error rate and latency percentiles per endpoint as a Markdown table.
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time
from collections import defaultdict

import httpx


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args()

    setup = {"X-Session-Id": "loadtest-setup"}
    with httpx.Client(base_url=args.base, timeout=60) as c:
        c.post("/sim/reset", headers=setup)
        people = [p["employee_id"] for p in c.get("/personas", headers=setup).json()]

    lat: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    lock = threading.Lock()
    stop = time.time() + args.seconds

    def worker(n: int) -> None:
        h = {"X-Session-Id": f"loadtest-{n:04d}"}
        with httpx.Client(base_url=args.base, timeout=60) as c:
            c.post("/sim/reset", headers=h)
            i = 0
            while time.time() < stop:
                calls = (
                    ("GET /health", lambda: c.get("/health")),
                    ("POST /advance/offer", lambda: c.post("/advance/offer", json={"employee_id": people[i % len(people)], "amount_bdt": 1_000 + 500 * (i % 6)}, headers=h)),
                    ("GET /ops/summary", lambda: c.get("/ops/summary", headers=h)),
                )
                for name, call in calls:
                    t0 = time.perf_counter()
                    try:
                        ok = call().status_code < 500
                    except httpx.HTTPError:
                        ok = False
                    with lock:
                        lat[name].append(time.perf_counter() - t0)
                        errors[name] += 0 if ok else 1
                i += 1

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(args.workers)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0

    total = sum(len(v) for v in lat.values())
    print(f"workers={args.workers} duration={wall:.0f}s requests={total} throughput={total / wall:.1f} req/s")
    print("| Endpoint | Requests | Errors | p50 (ms) | p95 (ms) | p99 (ms) | mean (ms) |")
    print("|---|---|---|---|---|---|---|")
    for name, v in lat.items():
        print(f"| {name} | {len(v)} | {errors[name]} | {pct(v, .5) * 1000:.0f} | {pct(v, .95) * 1000:.0f} | {pct(v, .99) * 1000:.0f} | {statistics.mean(v) * 1000:.0f} |")


if __name__ == "__main__":
    main()
