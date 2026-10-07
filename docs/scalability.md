# Scalability and integration readiness (Phase 2)

What exists today, what was measured, and what production would need. Everything runs on synthetic data.

## 1. Integration points

| Integration | Endpoint / module | What it does | Status |
|---|---|---|---|
| Payroll / HR export | `POST /employer/{id}/payroll-import?fmt=csv\|json&mapping=generic\|hr_export\|timecard` · `app/integrations/payroll.py` | Column-mapping adapters turn an employer's export into one record type; unpaid absence days update earned days; salary differences are listed for HR review, never applied silently | Built + tested |
| Time-card webhook | `POST /employer/{id}/attendance` | Attendance system pushes unpaid absence days; the next decision uses them | Built + tested |
| Payday settlement | `GET /employer/{id}/deduction-notice.csv`, `POST .../confirm` | One deduction list and one bulk remittance per payday | Built (Phase 1) |
| upay wallet / ledger | `app/ledger` (double-entry, integer paisa) | Mirrors wallet credits/debits; production would call upay's wallet and ledger APIs instead | Simulated |
| upay KYC | — | Employee identity comes from the employer's roster; production would check upay KYC status before the first advance | Not built (next step) |
| LLM explanations | `app/llm/explain.py` | Optional; numbers checked, template fallback | Built (Phase 1) |

Adding an HR system means registering a new `ColumnMap` in `MAPPINGS`, not writing a new code path. Real column names are an [ASSUMPTION] to confirm at onboarding.

## 2. Observability

- `GET /metrics`: Prometheus text format: request counts by method / route template / status, latency p50/p95/p99 per route, decisions by outcome, uptime. Route templates are used as labels, so employee IDs never appear in metrics (tested).
- One JSON access-log line per request (`request_id`, route, status, ms), no bodies or personal data. `X-Request-Id` is accepted or generated and returned.
- `GET /health` for the platform health check (Render uses it).

## 3. Load test (measured)

`python -m scripts.load_test --workers N --seconds S` against one local Uvicorn process (8 logical CPUs, Windows laptop, SQLite). Each worker has its own sandbox session. Mix per loop: health, an advance decision (all rules + M1/M2/M3/M5 + SHAP reasons), ops summary.

| Run | Endpoint | Requests | Errors | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| 1 worker, 15 s | POST /advance/offer | 44 | 0 | 351 ms | 394 ms | 425 ms |
| 1 worker, 15 s | GET /ops/summary | 44 | 0 | 28 ms | 42 ms | 117 ms |
| 10 workers, 47 s | POST /advance/offer | 269 | 0 | 1,216 ms | 2,025 ms | 2,158 ms |
| 10 workers, 47 s | GET /ops/summary | 269 | 0 | 391 ms | 773 ms | 1,030 ms |
| 10 workers, 47 s | all (807 requests) | 807 | **0** | 17.3 req/s overall | | |

Reading: no errors under 10 concurrent users, but one process serialises the CPU-heavy decision (about 3 decisions/s per process at this concurrency). A worker asks for an advance a few times a month, so one process covers roughly a few thousand workers; beyond that, scale out.

## 4. Path to production scale

1. **Run more processes:** `uvicorn --workers N` or several instances behind a load balancer. The decision path is stateless apart from the database.
2. **Shared database:** the seed store is read through SQLAlchemy from `DATABASE_URL`, so it can point at PostgreSQL. Sandbox sessions are one SQLite file each (a demo feature); production state (advances, ledger, attendance) moves to PostgreSQL tables with the same schemas in `app/sim/tables.py` and `app/ledger`.
3. **Cheaper decisions:** cache the M1 employer snapshot (it changes twice a month), compute SHAP reasons only for the shown offer, and batch employee history reads.
4. **Monitoring:** scrape `/metrics`; alert on decision p95, error rate, decline-share drift and the M4 pool shortfall.
5. **Reconciliation:** the payday waterfall and ledger already reconcile per session; production runs it as a scheduled job per employer payday with a daily report.

## 5. Not done today (stated plainly)

Real upay wallet/KYC APIs, PostgreSQL migration, multi-instance load tests, penetration testing.
