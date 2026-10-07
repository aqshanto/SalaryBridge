# Phase 2: what changed in response to the judges

Phase 1 score: 77.7 / 100. Each row links a judge comment to the change and to the evidence (file, test or generated number).
All numbers come from `docs/validation_report.md`, `backend/artifacts/metrics.json` or `docs/scalability.md`, regenerated on this branch. All data remains synthetic.

## Summary

| # | Change | Main categories |
|---|---|---|
| 1 | Removed employer headcount from M1, M2 and M3 (v2), re-ran the fairness audit | AI/ML depth, Responsible AI |
| 2 | Pricing engine: employer co-pay, tiered worker fee, payroll-linked fee; viable mix found | Business impact, Problem relevance |
| 3 | M4 v3: separate conformal widening for peak and calm months | AI/ML depth |
| 4 | upay brand identity in the UI | Prototype quality |
| 5 | Dependency guard + attendance-driven limits (time-card webhook) | Innovation, Responsible AI |
| 6 | Payroll adapters, `/metrics`, structured logs, measured load test | Scalability & integration |
| 7 | This document: model ablation, pilot plan, security checklist | All |

## 1. Problem relevance

| Judge comment (paraphrased) | Change | Evidence |
|---|---|---|
| Validate the salary-advance assumptions against real MFS data; fix the fee deficit | Fee deficit addressed by the pricing engine (§3). Real-data validation needs upay data, so a structured pilot is designed (§8) | `app/services/pricing.py`; §8 below |
| Validate the problem with real upay customers through a pilot study | Pilot plan with KPIs, go/no-go thresholds and a shadow-mode month | §8 |
| Strong fit for upay (earned-wage access, mid-month liquidity) | Kept; the payroll-import and attendance integrations make the employer side concrete | `docs/scalability.md` |

## 2. AI/ML depth

| Judge comment | Change | Evidence |
|---|---|---|
| Resolve M4 band over-conservatism in non-peak months | M4 v3 (Mondrian split-conformal): widening sized separately for peak months (Eid or the month after) and calm months | Profile A world coverage **92.2% → 85.6%** (target 75–85%), band **12% narrower**, pool still covers **100%** of test months; Profile B 75.6% → 77.8%. v2 numbers kept in `metrics.json` (`previous_version_v2`). Still 0.6 pp above the band; not tuned further on test data |
| Retrain M2/M3 without employer headcount; fix the employer-size fairness gap | Headcount removed from M1, M2 and M3 (M1 also used it and fed M2/M3 through employer risk) | Worst gap **9.13 pp → 5.25 pp**; Profile B **8.75→4.88 pp, now PASS**. Loss rate improved: A **0.62% → 0.59%**, B **1.31% → 1.19%**; M1 ROC-AUC **0.905 → 0.914** |
| Compare against more baselines; simplify the stack if some models do not improve decisions | Ablation below (§6), with a recommendation | §6 |

## 3. Business / customer impact

| Judge comment | Change | Evidence |
|---|---|---|
| Revise the fee structure or add employer co-pay | Pricing engine compares seven revenue mixes on the measured cost base (same formula as the sliders) | Report "Pricing scenarios"; Validation page table and two new sliders |
| ৳10,876 monthly loss at ৳25; need a viable pricing / subsidy model | **Recommended mix:** tiered worker fee (৳15 / ৳25 / ৳40 by advance size, average ≤ ৳25) + ৳20 employer co-pay + ৳10 payroll fee per salary paid through upay | **+৳16,436 / month (A), +৳2,660 / month (B)**. At ৳25 flat the v2 models lose ৳10,044 (A) |
| Test alternative pricing, employer co-payment and payroll-linked revenue | All three are scenarios; a worker-only slab (৳30/৳50/৳70) shows fees alone fail under stress | Worker-only slab: +৳185 (A) but −৳7,248 (B) with an average fee of ৳42–48. `tests/test_pricing.py` |

Prices are [ASSUMPTION]s and uptake is assumed equal across scenarios; the live product fee is unchanged until a pilot confirms willingness to pay.

## 4. Prototype quality

| Judge comment | Change | Evidence |
|---|---|---|
| Use upay's brand colour and identity | Navy #002447 header, yellow #FFC72C accents, logo blue #0F58A8 for actions and charts (light + dark) | `frontend/app/globals.css`, `frontend/components/Header.tsx` |
| Less new UI, more production integration and validation | Phase 2 added one table and two sliders; the rest is backend integration, validation and docs | This document |
| Integrate sandbox wallets and payroll files; test performance and reliability | Payroll-file import + time-card webhook; load test with zero errors | `docs/scalability.md` §1, §3 |

## 5. Innovation

| Judge comment | Change | Evidence |
|---|---|---|
| Dynamic limits that adapt to attendance / time-card webhooks | `POST /employer/{id}/attendance` and `payroll-import`: unpaid absence days lower earned days on the next request (rule input, never a model feature) | `tests/test_employer.py::test_attendance_webhook_lowers_the_limit_and_rejects_other_staff`, `tests/test_rules.py::test_attendance_feed_lowers_earned_days` |
| Differentiate with employer-level risk intelligence / adaptive capital allocation | Employer risk (M1) drives limits and the ops table; M4 v3 adapts the capital pool to peak vs calm months | §2; Ops view |
| Do not encourage vulnerable users into repeated dependency | **Dependency guard:** advances in 4 of the last 6 months (gaps allowed) halve the limit and show a savings nudge; never declines on its own | `tests/test_rules.py::test_dependency_guard_halves_the_limit_for_habitual_use_with_gaps`; EN/BN explanation in `app/llm/explain.py` |

## 6. Model ablation (do all models earn their place?)

Loss rate of approved advances when the same share is declined by each ranking (full amounts; lower is better):

| Ranking | Profile A (decline 9.9%) | Profile B (decline 20%) |
|---|---|---|
| Random (= flat cap) | 0.95% | 1.55% |
| Tenure rule (simple baseline) | 0.81% | 1.23% |
| M2 repayment risk | 0.65% | 1.41% |
| M3 leaving risk | 0.62% | **1.09%** |
| M2 + M3 combined | **0.61%** | 1.34% |

| Model | Role | Test ROC-AUC A / B | Verdict |
|---|---|---|---|
| M1 employer late payroll | Employer risk → limits, ops table | 0.914 / 0.849 | Keep: strongest model, and also feeds settlement planning |
| M2 repayment failure | Tier A–D (shrinks the offer) | 0.873 / 0.802 | **Weakest under stress**: in Profile B it ranks worse than the tenure rule. Candidate to merge with M3 |
| M3 leaving before payday | Downgrades a tier by one step | 0.827 / 0.774 | Keep: best ranking in the stress world |
| M4 capital forecast | Pool size (P10–P90) | coverage in §2 | Keep: no simpler baseline sizes the pool (MAE P50 far below previous-month baseline) |
| M5 borrowing-pattern monitor | Sends unusual patterns to a person | unsupervised | Keep: cheap, only routes to humans |

**Recommendation for the pilot:** replace M2 + M3 with one model trained on "not recovered or left before payday", and keep the tenure rule as a guard-rail baseline. Not changed today, so that the validated Phase 1 → 2 comparison stays clean.

## 7. Scalability & integration

| Judge comment | Change | Evidence |
|---|---|---|
| Payroll software adapters for HR onboarding | Column-mapping adapters (generic, HR export, time-card) for CSV/JSON | `app/integrations/payroll.py`, `tests/test_integration_ready.py` |
| Add payroll, KYC and wallet/ledger services; observability and scaling | Payroll import built; ledger is double-entry; KYC listed as the next step. `/metrics` (Prometheus), JSON access logs, request IDs | `app/observability.py`, `docs/scalability.md` |
| Synthetic data and SQLite: prove operational readiness, load testing, reconciliation | Load test: 10 concurrent workers, 807 requests, **0 errors**; decision p50 351 ms (1 user), 1.2 s (10 users, one process). Path to PostgreSQL and multiple processes documented | `scripts/load_test.py`, `docs/scalability.md` §3–4 |

## 8. Pilot plan (real-data validation)

| Item | Plan |
|---|---|
| Who | 2–3 employers (one garment, one retail, one services), 300–500 consenting workers paid through upay |
| Length | 3 payroll cycles. Month 1 in **shadow mode**: models score but only rules decide; compare predicted vs actual |
| Data | Payroll export + attendance feed (adapters above), upay wallet transactions with consent; no gender/region in models |
| KPIs | Uptake; recovery on payday; loss rate; employer remittance on time; share of users hitting the dependency guard; repeat-use trend; employer-size and gender approval gaps on real data; worker survey on stress and fee fairness |
| Go / no-go | Loss rate ≤ 1.5%; employer remittance on time ≥ 95%; approval gaps ≤ 5 pp or explained; dependency-guard share not rising month on month; net margin ≥ 0 under the chosen price mix |
| Pricing test | Randomise employers between the recommended mix and the employer-co-pay-only mix; measure uptake and willingness to pay |

## 9. Responsible AI & security checklist

| Control | Status | Evidence |
|---|---|---|
| Protected attributes never model inputs; employer headcount removed (v2) | Done | `tests/test_responsible_ai.py`, `FEATURES` in `app/ml/m1–m3` |
| Fairness audit by gender, region, employer size each run, with analyst notes | Done | `docs/validation_report.md` |
| Only rules decline; models shrink or route to a person; kill switch | Done (Phase 1) | `docs/responsible_ai.md` |
| Dependency guard against repeated borrowing | Done | §5 |
| HR never sees risk scores; webhook and import accept only the employer's own staff | Done | `tests/test_employer.py`, `tests/test_integration_ready.py` |
| Upload limits and validation (size, row count, numeric checks) | Done | `app/integrations/payroll.py`, `app/routers/employer.py` |
| No personal IDs in metrics or logs | Done | `tests/test_integration_ready.py` |
| No secrets in the repository; LLM optional with checked numbers | Done (Phase 1) | `tests/test_config.py` |
| Authentication and role-based access (today: sandbox session header only) | **Next step** | upay SSO/OAuth with roles employee / HR / ops |
| Rate limiting, penetration test, access-control review | **Next step** | Before any real money moves |
| Regulatory review (Bangladesh Bank digital credit / MFS rules), model governance and drift monitoring | **Next step** | Model cards exist in `metrics.json`; drift alerts via `/metrics` |
