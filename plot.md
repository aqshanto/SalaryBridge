# SalaryBridge: Plot (Story, Personas, Screens, Demo)

This file defines what the user experiences. `plan.md` defines how it works. Every feature in `prompt.md` must serve a scene here.

All names are fictional and all data is synthetic.

---

## 1. The one-line story

> "It's the 25th. Rahim's salary comes on the 1st. His daughter is sick and he needs 5,000 taka today. Instead of a moneylender, upay gives him part of the salary he has already earned, safely. On payday it is settled automatically, with one transfer from his employer."

## 2. Personas

| Persona | Role | Wants | Sees |
|---|---|---|---|
| **Rahim** | Garment worker, salary 18,000 BDT, tenure 3 years, employer reliable | Emergency cash, no shame, no paperwork | Employee view |
| **Shapla** | IT support, salary 50,000 BDT, tenure 4 months, employer often late | Wants 10,000 | Employee view (limited offer, with reasons) |
| **Karim** | Retail worker who has taken an advance 6 months in a row | Another advance | Employee view (cooling-off, with a gentle explanation) |
| **Nasrin** | HR manager at "Meghna Garments (fictional)" | Minimal work | Employer HR view |
| **Tanvir** | upay ops analyst | Control risk and capital | Ops view |

## 3. Screens

### 3.1 Employee view (mobile-sized web page, EN/BN toggle)

**Home card**
- "Earned so far this month: 15,000 BDT"
- "You can get up to: 3,600 BDT"

**Request**
- Amount slider.
- Fee and repayment date are shown before confirming.

**Offer explanation.** Up to 3 short reasons, for example:
- "Your employer pays on time"
- "You have repaid every advance"
- "Limit is based on days already worked"

**Confirmation.** The wallet balance animates up.

**Declined or limited.** A respectful message gives the reason and the next eligible date. It never says "you are risky".

**History.** Past advances and how each was settled.

### 3.2 Employer HR view

- **Dashboard:** employees using the advance, the total to deduct this payday, and the payday date.
- **Deduction notice:** one list (name, amount) and one total. Buttons: "Download CSV" and "Confirm remittance (simulated)".
- **Settings:** max cap %, opt-in toggle, payroll day.
- **No individual risk scores are shown to the employer** (privacy).

### 3.3 upay ops view

- **KPIs:**
  - outstanding advances;
  - capital pool vs P90 required;
  - loss rate;
  - approval rate.
- **Employer reliability table:** the M1 score, the trend, and SHAP reasons.
- **Approval queue:**
  - tier-D, large or anomaly-flagged requests;
  - approve or reject buttons, each with a required note.
- **Capital forecast chart:** next 3 months, P10/P50/P90 band, with the current pool overlaid.
- **Kill switch:** pause all new advances.
- **Abuse panel:** M5 flags.

### 3.4 Simulator bar (present on all views)

- **Calendar:** current simulated date, plus buttons for "+1 day", "+7 days" and "Jump to payday".
- **Scenario buttons:**
  1. "Employer pays late"
  2. "Employee resigns before payday"
  3. "Eid month surge"
  4. "Chronic borrower"
  5. "Employer defaults"
- **Reset simulation.**
- **Ledger panel:** a live money-flow diagram (`upay pool → employee wallets → employer remittance → upay pool`). Balances must always reconcile.

### 3.5 Validation page (for judges)

- ML vs flat-cap policy: a loss-rate vs approval-rate chart.
- Calibration plots.
- P10–P90 coverage.
- Profile B stress-test results.
- Fairness table.
- The assumptions list.
- Economics sliders with the break-even fee.

## 4. Scenarios the simulator must support

| # | Scenario | Expected system behaviour |
|---|---|---|
| S1 | Happy path (Rahim) | Approved, paid out, recovered by bulk remittance on payday; ledger reconciles |
| S2 | Risky employer (Shapla) | Smaller limit; reasons cite employer lateness; recovered late within grace |
| S3 | Resigns before payday | Recovery waterfall: employer final settlement → wallet auto-debit → carry-over or write-off; loss counted |
| S4 | Employer defaults | M1 score drops; new advances for that employer pause; exposure shown |
| S5 | Chronic borrower (Karim) | M5 flag; cooling-off; supportive message; limit reduces |
| S6 | Eid surge | M4 forecast band rises; ops sees pool vs P90; requests above pool go to the queue |
| S7 | Kill switch | All new requests are blocked with a clear message; existing advances still settle |

## 5. Demo script (3 minutes)

| Time | Beat |
|---|---|
| 0:00–0:20 | **Hook.** Rahim's story; the problem with moneylenders; the hypothesis. |
| 0:20–0:50 | **Employee view.** Rahim requests 5,000 and gets an offer with reasons; the wallet updates. |
| 0:50–1:10 | **Shapla.** Same request, smaller offer. Show the reasons: AI tiers within the rule caps. |
| 1:10–1:30 | **HR view.** One deduction list, one transfer. Point out "No risk scores shown to employer". |
| 1:30–1:50 | **Jump to payday.** The ledger flows back and reconciles. |
| 1:50–2:20 | **Ops view.** Trigger "Employer defaults" and "Eid surge": the reliability score drops, the capital band rises, and the queue fills. |
| 2:20–2:50 | **Validation page.** ML vs flat cap, calibration, Profile B, fairness. Read numbers only from the generated report. |
| 2:50–3:00 | **Close.** Honest limits (synthetic data, legal review, employer interviews) and the next step (controlled validation). |

## 6. Tone rules for all user-facing text

- Respectful and simple. No shame and no fear.
- Always show the fee and repayment before confirming.
- Never encourage borrowing more than needed.
- Bangla text is short and conversational. The English text is the source of truth.
