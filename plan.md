# SalaryBridge: Project Plan

SalaryBridge is a prototype that lets a worker get a small part of their already-earned salary early, with ML used for the risky decisions. It is built for upay (an MFS in Bangladesh) for the DIU CPC x upay AI Hackathon 2026.

- **Primary track:** Track 03 (Customer Innovation & Financial Independence, the "responsible credit readiness" direction).
- **Secondary:** Track 04/05-style forecasting of how much capital upay must hold.

Labels used throughout:

| Label | Meaning |
|---|---|
| **[SOURCE]** | Published fact |
| **[ASSUMPTION]** | We assume it; not yet validated |
| **[TARGET]** | Our own goal, not a proven result |

We are validating a **hypothesis**. We are not signing a real employer. Employer behaviour is **simulated**, and is itself modelled by ML.

---

## Phase 2 plan (judge feedback, branch `phase2-feedback`, deadline 12:00)

Phase 1 score 77.7/100. Work order: **1 → 2 → 3 → 6 → 4 → 5 → 7**, one commit per item, merged to `main` at the end. Tests are run only where a change needs them; the full suite runs at the end if time allows.

| # | Item | Judge feedback it answers | Done when |
|---|---|---|---|
| 1 | **Fairness fix:** retrain M2/M3 without `employer_headcount`, re-run the fairness audit | AI/ML J1/J3, Responsible AI J1/J3 (9.13 pp employer-size gap) | New metrics + audit regenerated; before/after table in the report |
| 2 | **Pricing engine:** employer co-pay, fee tiers, payroll-linked revenue; break-even per scenario | Business J1/J2/J3, Problem J1 (−৳10,876/month at ৳25) | API + Validation-page panel show at least one viable configuration |
| 3 | **M4 recalibration:** separate conformal widening for Eid and non-Eid months | AI/ML J1 (over-conservative band in non-peak months) | T2-A coverage re-checked; before/after band width reported |
| 6 | **upay brand colours** in the UI | Prototype J2 | Theme tokens updated, screenshots unchanged in layout |
| 4 | **Dependency guard + attendance-driven limit:** repeat-use safeguard and a simulated attendance webhook that resizes the limit | Innovation J1/J2/J3, Responsible AI J3 | Rule + endpoint + test |
| 5 | **Integration readiness:** payroll adapter interface (CSV/JSON), `/metrics`, short load test, Postgres-ready `DATABASE_URL` | Scalability J1/J2/J3 | Adapter + endpoint + load-test numbers in docs |
| 7 | **`docs/phase2_changes.md`:** every judge comment → change → evidence; pilot plan, model ablation, security checklist | All categories | Doc linked from README |

Out of scope today (stated honestly as next steps): real upay data, penetration testing, real KYC/wallet integration.

---

## 1. Problem and hypothesis

**Problem statement:** Salaried workers who run out of money before payday face an emergency (for example, 5,000 BDT on the 25th). That pushes them to informal lenders, or to ask their employer for an advance.

We will build a prototype where upay offers a small advance on already-earned wages that is recovered automatically. ML decides three things:
- how much is safe for each person,
- how reliable each employer's payroll is,
- how much capital upay must hold next month.

Success means a low simulated loss rate while still approving most eligible requests, plus a calibrated capital-need forecast.

**Hypotheses (validated in simulation, not assumed true):**
- **H1.** A small advance, capped at a share of already-earned wages, can be recovered at a low loss rate if limits are risk-tiered by employer reliability and by the employee's risk of quitting.
- **H2.** ML tiering beats a flat cap (for example, "20% of salary for everyone") on loss rate, at an equal approval rate.
- **H3.** Next-month capital need can be forecast with a calibrated P50/P90 range.
- **H4.** Recovery through one bulk employer deduction per month is low-friction for employer HR.

**Known facts [SOURCE]**
- upay launched on 17 Mar 2021 and is a subsidiary of UCB Fintech.
- It has a salary wallet (part of its multi-wallet feature) and a salary disbursement product.
- TBS News reported about 7M customers, 100k+ agents and about 35% account activity. These figures may be outdated.

**Unknown (state openly in the pitch):**
- the regulatory treatment of earned-wage advances in Bangladesh;
- real fees and limits;
- real employer appetite;
- real default rates.

## 2. Why an employer would cooperate (to be field-tested later)

**[ASSUMPTION]** The employer gets:
- better retention;
- fewer ad-hoc advance requests to HR;
- no cost and no credit risk;
- one monthly reconciliation file instead of many individual requests.

The model treats employer cooperation as a **risk**, not a given:
- employer reliability is a modelled variable;
- employer friction is kept minimal (one list, one transfer).

**Post-hackathon:** interview 3–5 HR managers.

## 3. Scope

**In scope:**
- simulated employers, employees and payroll;
- a double-entry ledger;
- a rule engine;
- five ML components;
- a decision service;
- a month simulator;
- three role UIs (employee, employer HR, upay ops);
- a validation report page;
- economics sliders;
- deployment on Vercel and Render.

**Out of scope:**
- real payments, real KYC, real customer data;
- autonomous approval or denial of consequential decisions;
- any claim of legal compliance.

## 4. Flow

1. An employer is onboarded in the simulation, with a payroll day and an employee list.
2. An employee requests an advance, for example 5,000 BDT on day 25.
3. The decision service runs:
   - eligibility rules,
   - the ML risk tier,
   - the offer (max safe amount, fee, repayment date),
   - the explanation.

   Large or flagged cases go to human approval.
4. The ledger records `upay_pool → employee_wallet`.
5. Employer HR sees **one consolidated deduction notice** (one list, one total).
6. On payday, the employer pays net salary and remits the total deduction in **one transfer**: `employer_remittance → upay_pool`.
7. Recovery happens in this order (the recovery waterfall):
   1. employer bulk remittance;
   2. an employee-consented wallet auto-debit, if the salary lands in upay;
   3. carry-over to the next month, with a reduced limit;
   4. a write-off provision.
8. The ops dashboard updates exposure, losses, the capital pool and employer reliability scores.

## 5. Who owns which decision

| Task | Owner |
|---|---|
| Hard cap = min(cap% × monthly salary, earned-to-date wages − already advanced) | Rules |
| Fee, repayment date, deduction order | Rules |
| Probability the employer pays late | ML (M1) |
| Employee repayment-failure risk, mapped to a safe-limit tier | ML (M2) |
| Risk the employee leaves before payday | ML (M3) |
| Next-month advance volume and capital pool (P10/P50/P90) | ML quantile (M4) |
| Abuse, chronic borrowing and gaming detection | ML anomaly (M5) + rules |
| Approvals that are large, flagged or tier D | Human |
| Plain-language explanation | LLM, given structured JSON only; it never decides |

All numeric policy parameters live in `backend/app/config.py` and `docs/assumptions.md`, labelled **[ASSUMPTION]**.

## 6. Architecture and deployment

```
salarybridge/
  backend/                FastAPI + SQLAlchemy + SQLite (swappable via DATABASE_URL)
    app/
      main.py config.py db.py
      routers/ services/ ml/ ledger/ rules/ llm/ sim/
    data/                 generator.py (profiles A and B), seed.py
    artifacts/            trained *.joblib + metrics.json (committed to git)
    tests/
  frontend/               Next.js (App Router, TypeScript, Tailwind, Recharts)
  docs/                   assumptions.md, validation_report.md, demo_script.md
```

**Hosting**
- **Frontend on Vercel.** `NEXT_PUBLIC_API_BASE_URL` points to the Render backend.
- **Backend on Render** (web service). Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

**Database**
- `seed.db` is read-only. It is regenerated at startup from a fixed random seed if it is missing.
- Each browser session has its own `sim_state`: an SQLite file in a temp dir, keyed by the `X-Session-Id` header, with TTL cleanup.
- Render's free-tier disk is ephemeral, so **everything must be reproducible from the seed**.

**Runtime notes**
- **ML artifacts** are trained offline, committed and loaded at startup. There is no training at deploy time.
- **Cold start:** free Render services sleep when idle. Hit `/health` about 10 minutes before a demo.
- **CORS allow-list:** the Vercel domain plus localhost.
- **Secrets:** env vars only (`ANTHROPIC_API_KEY` is optional; there is a template fallback if it is absent).
- **Reset:** `POST /sim/reset` rebuilds the session state from the seed.
- Check current Render/Vercel free-tier limits before deploying.

## 7. Synthetic data design (all synthetic; every assumption documented)

**Employers (~40)**
- Industries: garments, retail, IT, NGO, factory, hospital, school.
- Each has a latent reliability type: on-time, sometimes late, often late, or default-risk.
- Each also has a size and a payroll day.

**Employees (~6,000)**
- Attributes: salary, tenure, region (urban/rural), gender, age band.
- **Gender and region are used only for fairness auditing. They are never model features.**

**History: 24 months.** Payroll runs (with delays), earned days, advance requests, repayments and resignations.

**Injected patterns**
- surges in requests at month-end and before Eid;
- low-salary workers request more often;
- employer lateness persists from month to month;
- short-tenure workers have higher attrition;
- a small group of chronic borrowers;
- a small group of abusers who game the system.

**How losses happen.** Loss events arise mechanically:
- the employer defaults, or pays later than the grace period;
- the employee resigns before payday;
- abuse.

**Data splits**
- **Profile A** is used for training and validation.
- **Profile B** uses different parameters and noise, and is a held-out stress test.
- **The last 3 months are a time-based test set** and are never used in training.

## 8. ML components

| # | Model | Target | Algorithm | Baseline | Metrics |
|---|---|---|---|---|---|
| M1 | Employer reliability | Payroll is late beyond the grace period next month | LightGBM + calibration | Last month's lateness | PR-AUC, Brier, calibration |
| M2 | Repayment-failure risk → tier | Advance not recovered by payday + grace | LightGBM + calibration | Flat 20% cap / tenure rule | PR-AUC, Brier, loss at equal approval |
| M3 | Attrition before payday | Employee resigns before next payday | LightGBM + calibration | Tenure rule | PR-AUC, calibration |
| M4 | Capital need forecast | Next-month total advances outstanding | LightGBM quantile (P10/P50/P90) | Previous month / seasonal naive | MAE, pinball loss, P10–P90 coverage ≈ 80% |
| M5 | Abuse / anomaly | Chronic or gaming behaviour | Isolation Forest + rules | Rule threshold | Precision@k on planted abusers |

**How the tier is built.** The tier combines M1–M3 through **transparent rules**, not a black-box blend, so every limit can be explained. M1–M3 are also explained with SHAP.

**Capital pool:**
```
required_pool = P90(next-month outstanding) × (1 + buffer%)
```
`buffer%` is an [ASSUMPTION].

## 9. Validation plan (5 layers)

1. **Problem validation (outside the code).**
   - Interview 3–5 workers and 2–3 HR contacts.
   - Report the counts honestly. If the evidence is weak, say so.
2. **Data validation.**
   - `assumptions.md`;
   - sanity checks on distributions and seasonality;
   - a time-based split;
   - no leakage (features use only data from before the decision date);
   - comparison of Profile A vs B.
3. **Model validation.**
   - baseline comparison and calibration curves;
   - ablations;
   - stress tests (an employer-shock month, an Eid month);
   - new employees with little history (cold start);
   - a SHAP sanity check.
4. **Policy simulation.** Replay the test window under three policies: no product, flat cap, and ML tier. Report:
   - approval rate and funds deployed;
   - loss rate;
   - recovery rate by waterfall step;
   - the capital pool vs actual peak exposure.
5. **Responsible AI and security.**
   - a fairness audit by gender, region and employer size (gaps in approval rate and loss rate);
   - a human-approval log;
   - LLM prompt-injection tests;
   - a kill switch.

**Targets [TARGET]. These are declared before running and must not be tuned afterwards.**
- The ML-tier loss rate is lower than the flat-cap loss rate, at an approval rate at least as high.
- M4's P10–P90 coverage is between 75% and 85% on the test window.
- No group's approval-rate gap exceeds the pre-declared threshold without a written explanation.

## 10. Economics (every input is a slider, every input is an [ASSUMPTION])

```
revenue = Σ fees
cost    = expected_loss + pool × days × capital_rate + ops_cost
net     = revenue − cost
```

- **Sliders:** fee, cap %, grace days, capital rate, buffer %, employer-lateness shift.
- **Also show:** the break-even fee.
- Never present a single number as fact.

## 11. Responsible AI

- **Privacy:** synthetic data only.
- **Explainability:** reasons shown on every offer.
- **Fairness:** the audited groups are never used as model features.
- **Human oversight:** tier D, large or flagged requests go to ops for approval.
- **Security:**
  - the LLM sees structured JSON only;
  - user free text never reaches decision logic.
- **Transparency:** predictions, assumptions and generated text are visibly separated.
- **No hidden fees.**
- **Debt-trap guard:** a cooling-off period and limit reduction for chronic borrowers.

## 12. Defence Q&A

**Isn't this a loan?**
- We do not know the regulatory treatment.
- The design is small, short-term, capped by earned wages, recovered automatically, and has human oversight.
- A legal review is a prerequisite for any pilot.

**Why would an employer agree?**
- This is unproven, so we simulate it and treat employer reliability as a modelled risk.
- Friction is minimal: one list and one transfer per month.
- HR interviews are planned.

**Your data is synthetic.**
- Correct, so we validate the method, not real-world results:
  - a time-based split;
  - a second data profile;
  - baselines;
  - calibration.
- Real-data validation is the post-hackathon stage.

**Why ML and not rules?** Rules set the hard limits, and ML tiers within them. We show ML beats the flat cap on loss at an equal approval rate.

**Who bears the loss?**
- That is a policy question for upay/UCB.
- We quantify expected loss and the capital required.

**Debt trap?** The earned-wage cap, cooling-off period, chronic-borrower detection and transparent fees address it.

**LLM risks?**
- The LLM never decides, and gets structured input only.
- A template fallback exists.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Regulatory unknowns | State them openly; legal review before any pilot |
| Employer non-cooperation | Model employer reliability; minimise HR friction |
| Model overfits its own synthetic world | Profile B stress test; time-based split |
| Scope creep | One feature at a time, in a fixed order |
| Free-tier cold starts | Warm `/health` before the demo |

## 14. Red lines (never claim)

- "Employers will agree."
- "This works on real upay data."
- "These fees or limits are upay policy."
- "This is legal or compliant."
- "AI approves loans."
- Any specific result number before the code has produced it.
