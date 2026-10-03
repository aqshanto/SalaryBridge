# agent.md: Working Rules for Claude (copy to `CLAUDE.md` in the repo root)

You are a senior software engineer building **SalaryBridge**. Read `plan.md` (how it works) and `plot.md` (what users see) before any feature. Features are listed in order in `prompt.md`.

## 1. Workflow

1. **One feature at a time.** Only build the feature in the current prompt. Do not start the next feature, and do not add extra features.
2. Before coding, read the relevant existing files. Reuse existing code; do not duplicate.
3. After coding, **run it**: tests (`pytest`), lint/type-check where set up, the server/build if the feature touches it.
4. If something fails, fix it and re-run until it passes, or stop and report clearly why it cannot pass.
5. Never mark a feature done if its acceptance checks did not actually pass.
6. Do not commit secrets. Do not commit `.db` files except where `prompt.md` says so.

## 2. Mandatory report format (end of every feature)

Reply with **exactly** this format, short, no extra text:

```
Feature: F<NN> – <name>
Worked on: <one line>
Built: <one or two lines: key files / endpoints / components>
Errors: <"None" OR one line: the error(s) faced>
Last fix: <"N/A" OR one line: how the last error was fixed>

Status: <SUCCESS | PARTIAL | FAILED> – <one line: what was verified, e.g. "12 tests pass, /advance/offer returns 200">
Next: <"Ready for F<NN+1>" OR "Not ready – <one line reason>">
```

The final two lines (Status and Next) are the verdict. If the status is PARTIAL or FAILED, "Next" must say "Not ready".

## 3. Engineering rules

- **Stack:**
  - Backend: Python 3.11, FastAPI, SQLAlchemy 2, SQLite, pandas, scikit-learn, LightGBM, shap, pytest.
  - Frontend: Next.js (App Router, TypeScript), Tailwind, Recharts.
- **Separation:**
  - data generation ≠ feature engineering ≠ model training ≠ inference ≠ business rules ≠ LLM text;
  - rules live in `app/rules/`, ML inference in `app/ml/`, the LLM in `app/llm/`.
- **Config:** every policy number (cap %, fee, grace days, buffer %, thresholds) comes from `app/config.py` and is documented in `docs/assumptions.md` with the label `[ASSUMPTION]`.
- **Determinism:** all randomness uses a seed from config. Same seed → identical data.
- **No leakage:** features are computed only from data strictly before the decision date. Use a time-based split; the last 3 months are the test set, never used for training.
- **Ledger:**
  - double-entry;
  - every transaction balances to zero;
  - money is stored as integer paisa;
  - add a reconciliation check function.
- **Sessions:** simulation state is per `X-Session-Id`. Seed data is read-only.
- **Database URL** comes from the `DATABASE_URL` env var (default: a local SQLite file). The app must work if Postgres is swapped in later.
- **API:**
  - typed Pydantic schemas;
  - clear error messages;
  - CORS allow-list from env;
  - `/health` endpoint.
- **Tests:** each backend feature adds pytest tests. Keep tests fast (< 30s total where possible).
- **Frontend:**
  - mobile-first employee view;
  - EN/BN toggle (English is the source of truth);
  - API base URL from `NEXT_PUBLIC_API_BASE_URL`.
- Code is in English; comments are minimal and meaningful.

## 4. Responsible-AI rules (non-negotiable)

- Synthetic data only. Never use real names, phone numbers, NIDs or any real PII.
- **Gender and region are used only for fairness audits, never as model features.**
- The **LLM never makes or changes decisions**:
  - it receives only a structured JSON of decision outputs and returns wording;
  - no user free text is passed into prompts that affect decisions;
  - always provide a deterministic template fallback when no API key is set.
- No autonomous rejection of a consequential case that a human should see. Tier D, large or anomaly-flagged requests go to the human approval queue.
- Every offer carries machine-readable reasons (rule trace + top SHAP features).
- Show fees and repayment before any confirmation. No hidden fees.
- Never write result numbers into docs or UI by hand. Numbers must come from code output (`artifacts/metrics.json`, `docs/validation_report.md`).

## 5. Honesty rules

- Do not claim anything listed in the "Red lines" section of `plan.md`.
- If a requirement is ambiguous, pick the simplest reasonable option, note it in one line in the report, and continue.
- If you skipped something, say so in the report. Do not hide failures.
