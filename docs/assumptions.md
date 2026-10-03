# Assumptions

Everything in this file is an **[ASSUMPTION]**: a value we chose to make the simulation run. None of it is upay policy, real market data, or legal advice. Every policy value can be changed with an env var (`POLICY__<KEY_IN_UPPERCASE>`, for example `POLICY__CAP_PCT_OF_SALARY=25`) and is served by `GET /config/public`.

## 1. Policy parameters

The default column is checked against `backend/app/config.py` by a test, so this table cannot silently drift from the code.

| Key | Default | Label | Rationale |
|---|---|---|---|
| `cap_pct_of_salary` | 20.0 | [ASSUMPTION] | Upper bound of the 10–20% range the team discussed; the hard cap is also limited by wages already earned. |
| `min_advance_bdt` | 500 | [ASSUMPTION] | Below this the flat fee is a large share of the amount. |
| `min_tenure_days` | 90 | [ASSUMPTION] | Roughly a probation period; very new staff have no payroll history. |
| `max_advances_per_month` | 2 | [ASSUMPTION] | Allows one emergency plus one follow-up, limits fragmentation. |
| `fee_flat_bdt` | 25 | [ASSUMPTION] | A small, transparent flat fee; no interest-like percentage by default. |
| `fee_pct_of_amount` | 0.0 | [ASSUMPTION] | Kept at zero so the fee does not grow with need; available for economics sliders. |
| `grace_days` | 5 | [ASSUMPTION] | Tolerates a slightly late payroll before counting a recovery failure. |
| `carry_over_limit_reduction_pct` | 50.0 | [ASSUMPTION] | If an advance carries over, next month's limit is halved. |
| `writeoff_after_days` | 60 | [ASSUMPTION] | Unrecovered balance moves to the loss provision after two months. |
| `tier_a_max_risk` | 0.05 | [ASSUMPTION] | Tier A: predicted repayment-failure probability ≤ 5%. |
| `tier_b_max_risk` | 0.12 | [ASSUMPTION] | Tier B: ≤ 12%. |
| `tier_c_max_risk` | 0.25 | [ASSUMPTION] | Tier C: ≤ 25%; above this is tier D (human approval). |
| `tier_a_share_of_cap` | 1.0 | [ASSUMPTION] | Tier A may use the full hard cap. |
| `tier_b_share_of_cap` | 0.7 | [ASSUMPTION] | Tier B gets 70% of the hard cap. |
| `tier_c_share_of_cap` | 0.4 | [ASSUMPTION] | Tier C gets 40% of the hard cap. |
| `attrition_downgrade_threshold` | 0.3 | [ASSUMPTION] | If the predicted chance of leaving before payday is ≥ 30%, drop one tier. |
| `cooling_off_consecutive_months` | 3 | [ASSUMPTION] | Three advances in a row signals possible dependence. |
| `cooling_off_months` | 1 | [ASSUMPTION] | Then pause for one month, with a supportive message. |
| `large_amount_threshold_bdt` | 10000 | [ASSUMPTION] | Requests at or above this go to the human approval queue. |
| `abuse_flag_top_pct` | 2.0 | [ASSUMPTION] | The most unusual 2% of borrowing patterns (by M5 anomaly score in training data) go to a person before any offer. |
| `fairness_gap_threshold_pp` | 5.0 | [ASSUMPTION] | Approval-rate gap between audited groups (percentage points) that requires an explanation. |
| `initial_pool_bdt` | 5000000 | [ASSUMPTION] | Money upay sets aside for advances when a simulation session starts. |
| `capital_buffer_pct` | 15.0 | [ASSUMPTION] | Pool held above the P90 forecast. |
| `capital_rate_annual_pct` | 12.0 | [ASSUMPTION] | Cost of the money upay sets aside for advances. |
| `ops_cost_per_advance_bdt` | 5 | [ASSUMPTION] | Processing and messaging cost per advance. |

## 2. Synthetic world (Profile A, `backend/data/generator.py` → `ProfileParams`)

Values below are the defaults in `ProfileParams`. Change the code and this table together.

**How history is created.** Past advances were decided by a simple "pilot policy" made only of rules (tenure ≥ `min_tenure_days`, at most `max_advances_per_month`, amount ≤ min(`cap_pct_of_salary` × salary, wages earned so far)). This gives an outcome for every approved advance, which the ML models learn from later. Wages for work month M are paid on the employer's payroll day in month M+1; an advance taken in month M is due that payday.

| Assumption | Profile A value | Label |
|---|---|---|
| Calendar | 24 work months from 2024-10; the last 3 (2026-07 to 2026-09) are the test window | [ASSUMPTION] |
| Employers | 40; sizes from a Dirichlet split, minimum 10 staff each | [ASSUMPTION] |
| Employee positions | 6,000 positions; a leaver is replaced next month, so ~8,400 people appear over 24 months | [ASSUMPTION] |
| Industries (weight) | garments 25%, factory 15%, retail 15%, IT 15%, school 10%, NGO 10%, hospital 10% | [ASSUMPTION] |
| Salary ranges (monthly BDT, log-uniform, rounded to 500) | garments/factory 12k–25k, retail/school 15k–35k, NGO/hospital 20k–60k, IT 30k–120k | [ASSUMPTION] |
| Employer reliability mix (exact counts) | on-time 50%, sometimes late 30%, often late 15%, default-risk 5% | [ASSUMPTION] |
| Monthly chance payroll is late | on-time 3%, sometimes late 20%, often late 50%, default-risk 35% | [ASSUMPTION] |
| Lateness persistence | +15 percentage points if last month was late or partial | [ASSUMPTION] |
| Delay length (days) | on-time 1–3, sometimes late 1–6, often late 3–15, default-risk 3–20 | [ASSUMPTION] |
| Partial payroll | often late 10%, default-risk 15% of months; 50–90% of wages paid | [ASSUMPTION] |
| Employer default | default-risk employers: 8% per month; on default no wages are paid and the employer closes | [ASSUMPTION] |
| Payroll day | 1st (70%), 5th (20%), 7th (10%) | [ASSUMPTION] |
| Monthly request chance (normal staff) | 6% × min(2, √(25,000 / salary)); × 1.4 if tenure < 1 year; × 1.8 in Eid months; max 90% | [ASSUMPTION] |
| Synthetic Eid months | 2025-03, 2025-06, 2026-03, 2026-05 | [ASSUMPTION] |
| Request day | days 20+ are 4× as likely as earlier days | [ASSUMPTION] |
| Second request in a month | 15% | [ASSUMPTION] |
| Requested amount | normal staff: 30% ask for the full limit, 15% ask a round sum (1k, 2k, 3k, 5k, 8k or 10k BDT, which can exceed the limit), 55% ask 30–100% of the salary cap; abusers always ask for the full limit. (Changed in F12: when only abusers asked for the full limit, the models learned "full limit = abuser", which is unrealistic.) | [ASSUMPTION] |
| Chronic borrowers | 3% of people; 85% chance of a request every month | [ASSUMPTION] |
| Abusers | 1% of people; 50% monthly request chance; after an advance, 60% chance they resign within 1–5 days | [ASSUMPTION] |
| Attrition (monthly) | 3.5% if tenure < 1 year, otherwise 1.2% | [ASSUMPTION] |
| Absences | Poisson(1) days per month | [ASSUMPTION] |
| Salary paid into upay wallet | 30% of people | [ASSUMPTION] |
| Recovery step 1: employer remittance | full on payday (partial share if payroll is partial); for leavers, final settlement succeeds 50% of the time; nothing on default | [ASSUMPTION] |
| Recovery step 2: wallet auto-debit (with consent) | succeeds 50% if salary goes to upay, otherwise 15%; within the grace period | [ASSUMPTION] |
| Recovery step 3: carry-over | if still employed and the employer still pays: recovered next payday 80% of the time | [ASSUMPTION] |
| Recovery step 4: write-off | remaining balance written off `writeoff_after_days` after the due date | [ASSUMPTION] |
| Live simulation: spending | an advance is spent (cash-out or payments) the day after it is paid; wages paid into a upay wallet are spent during the month, leaving 10% of the last wage credit (`wallet_savings_share`) on the next payday | [ASSUMPTION] |
| Live simulation: wallet auto-debit consent | everyone who takes an advance consents to the wallet step of the recovery waterfall | [ASSUMPTION] |
| Live simulation: wages via upay | on payday the employer remits the total deduction once, and staff paid via upay receive net wages (gross minus their deduction) | Design rule |
| Model label ("recovery failure") | not fully recovered by due date + `grace_days` (includes late payroll beyond grace) | Design rule |
| Audit-only attributes | gender (share female by industry) and region (rural share by industry) are used only for fairness checks, never as model features | Design rule |

## 3. Things we explicitly do not know

- How Bangladesh regulation treats an earned-wage advance.
- Real upay fees, limits or employer agreements.
- Whether employers would agree to a monthly bulk deduction.
- Real default and attrition rates.
