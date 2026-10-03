"""Synthetic world generator for SalaryBridge.

Everything here is synthetic and every number is an [ASSUMPTION] listed in docs/assumptions.md.

History is generated under a simple "pilot policy" (rules only: tenure, monthly count, flat cap
limited by earned wages) so that outcomes exist for every approved advance. The ML tiers are
trained later on this history.

Timing: wages for work month M are paid on the employer's payroll day in month M+1. An advance
taken during month M is due on that payday.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.config import PolicyParams

INDUSTRIES = {
    # name: (salary_min, salary_max, weight, female_share, rural_share)
    "garments": (12_000, 25_000, 0.25, 0.60, 0.50),
    "factory": (12_000, 25_000, 0.15, 0.30, 0.60),
    "retail": (15_000, 35_000, 0.15, 0.35, 0.30),
    "school": (15_000, 35_000, 0.10, 0.50, 0.50),
    "ngo": (20_000, 60_000, 0.10, 0.45, 0.60),
    "hospital": (20_000, 60_000, 0.10, 0.55, 0.30),
    "it": (30_000, 120_000, 0.15, 0.25, 0.05),
}
RELIABILITY_TYPES = ("on_time", "sometimes_late", "often_late", "default_risk")
AGE_BANDS = ("18-25", "26-35", "36-45", "46-60")


@dataclass(frozen=True)
class ProfileParams:
    name: str = "A"
    start: date = date(2024, 10, 1)
    months: int = 24
    test_months: int = 3
    n_employers: int = 40
    n_employees: int = 6_000

    # Employers and payroll
    reliability_mix: dict = field(
        default_factory=lambda: {"on_time": 0.50, "sometimes_late": 0.30, "often_late": 0.15, "default_risk": 0.05}
    )
    late_prob: dict = field(
        default_factory=lambda: {"on_time": 0.03, "sometimes_late": 0.20, "often_late": 0.50, "default_risk": 0.35}
    )
    late_persistence: float = 0.15
    delay_days: dict = field(
        default_factory=lambda: {"on_time": (1, 3), "sometimes_late": (1, 6), "often_late": (3, 15), "default_risk": (3, 20)}
    )
    partial_prob: dict = field(default_factory=lambda: {"often_late": 0.10, "default_risk": 0.15})
    default_monthly_prob: float = 0.08
    payroll_day_weights: dict = field(default_factory=lambda: {1: 0.7, 5: 0.2, 7: 0.1})

    # Requests
    base_request_prob: float = 0.06
    ref_salary: int = 25_000
    short_tenure_multiplier: float = 1.4
    eid_multiplier: float = 1.8
    eid_months: tuple = ((2025, 3), (2025, 6), (2026, 3), (2026, 5))
    late_window_start_day: int = 20
    late_window_weight: float = 4.0
    second_request_prob: float = 0.15

    # Behaviour groups
    chronic_share: float = 0.03
    abuser_share: float = 0.01
    chronic_request_prob: float = 0.85
    abuser_request_prob: float = 0.50
    abuser_resign_after_advance: float = 0.60

    # Attrition
    attrition_short_tenure: float = 0.035
    attrition_long_tenure: float = 0.012

    # Recovery
    salary_to_upay_share: float = 0.30
    final_settlement_recovery_prob: float = 0.50
    wallet_debit_prob_salary_in_upay: float = 0.50
    wallet_debit_prob_other: float = 0.15
    carry_over_recovery_prob: float = 0.80

    # Multiplies every industry salary range
    salary_scale: float = 1.0


# Profile B is a held-out stress world: harsher employers, lower wages, earlier and shifted surges,
# more churn and more risky borrowers. Models never train on it.
PROFILE_B = replace(
    ProfileParams(),
    name="B",
    reliability_mix={"on_time": 0.35, "sometimes_late": 0.30, "often_late": 0.25, "default_risk": 0.10},
    late_prob={"on_time": 0.05, "sometimes_late": 0.25, "often_late": 0.55, "default_risk": 0.40},
    late_persistence=0.25,
    delay_days={"on_time": (1, 4), "sometimes_late": (2, 8), "often_late": (4, 20), "default_risk": (5, 25)},
    partial_prob={"sometimes_late": 0.03, "often_late": 0.15, "default_risk": 0.20},
    payroll_day_weights={1: 0.5, 5: 0.25, 7: 0.25},
    base_request_prob=0.08,
    eid_months=((2025, 4), (2025, 7), (2026, 4), (2026, 6)),
    late_window_start_day=15,
    late_window_weight=3.0,
    second_request_prob=0.20,
    chronic_share=0.05,
    abuser_share=0.02,
    attrition_short_tenure=0.05,
    attrition_long_tenure=0.018,
    salary_to_upay_share=0.20,
    final_settlement_recovery_prob=0.40,
    wallet_debit_prob_salary_in_upay=0.45,
    wallet_debit_prob_other=0.10,
    carry_over_recovery_prob=0.70,
    salary_scale=0.85,
)

PROFILES = {"A": ProfileParams(), "B": PROFILE_B}


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _round_down(x: float, step: int = 100) -> int:
    return int(x // step * step)


class _World:
    def __init__(self, p: ProfileParams, policy: PolicyParams, seed: int):
        self.p = p
        self.policy = policy
        self.rng = np.random.default_rng(seed)
        self.month_starts = [_add_months(p.start, i) for i in range(p.months)]
        self.employers: list[dict] = []
        self.runs: dict[tuple[str, int], dict] = {}
        self.employees: list[dict] = []
        self.attendance: list[dict] = []
        self.requests: list[dict] = []
        self.advances: list[dict] = []
        self.repayments: list[dict] = []
        self.resignations: list[dict] = []

    # ---------- employers and payroll ----------
    def build_employers(self) -> None:
        p, rng = self.p, self.rng
        names = list(INDUSTRIES)
        weights = np.array([INDUSTRIES[n][2] for n in names])
        industries = rng.choice(names, size=p.n_employers, p=weights / weights.sum())
        # Exact mix (largest remainder), then shuffled, so every world contains each employer type.
        quotas = {t: share * p.n_employers for t, share in p.reliability_mix.items()}
        counts = {t: int(q) for t, q in quotas.items()}
        for t in sorted(quotas, key=lambda t: quotas[t] - counts[t], reverse=True)[: p.n_employers - sum(counts.values())]:
            counts[t] += 1
        reliability = np.array([t for t in quotas for _ in range(counts[t])])
        rng.shuffle(reliability)
        days = list(p.payroll_day_weights)
        day_p = np.array([p.payroll_day_weights[d] for d in days])
        payroll_days = rng.choice(days, size=p.n_employers, p=day_p / day_p.sum())
        shares = rng.dirichlet(np.full(p.n_employers, 2.0))
        sizes = np.maximum(10, np.round(shares * p.n_employees)).astype(int)
        sizes[np.argmax(sizes)] += p.n_employees - sizes.sum()
        for i in range(p.n_employers):
            self.employers.append(
                {
                    "employer_id": f"E{i + 1:03d}",
                    "name": f"Employer {i + 1:02d} ({industries[i].title()})",
                    "industry": str(industries[i]),
                    "reliability_type": str(reliability[i]),
                    "payroll_day": int(payroll_days[i]),
                    "headcount": int(sizes[i]),
                    "opted_in": True,
                    "closed_month_index": None,
                }
            )

    def build_payroll(self) -> None:
        p, rng = self.p, self.rng
        for emp in self.employers:
            rtype = emp["reliability_type"]
            prev_late = False
            for i, ms in enumerate(self.month_starts):
                scheduled = _add_months(ms, 1).replace(day=emp["payroll_day"])
                status, delay, paid_share = "on_time", 0, 1.0
                if rtype == "default_risk" and rng.random() < p.default_monthly_prob:
                    status, paid_share = "default", 0.0
                else:
                    late_p = p.late_prob[rtype] + (p.late_persistence if prev_late else 0.0)
                    if rng.random() < late_p:
                        lo, hi = p.delay_days[rtype]
                        delay = int(rng.integers(lo, hi + 1))
                        status = "late"
                    if rng.random() < p.partial_prob.get(rtype, 0.0):
                        status, paid_share = "partial", float(np.round(rng.uniform(0.5, 0.9), 2))
                prev_late = status in ("late", "partial")
                self.runs[(emp["employer_id"], i)] = {
                    "employer_id": emp["employer_id"],
                    "work_month": ms.isoformat()[:7],
                    "month_index": i,
                    "scheduled_date": scheduled,
                    "actual_date": None if status == "default" else scheduled + timedelta(days=delay),
                    "delay_days": delay,
                    "status": status,
                    "paid_share": paid_share,
                }
                if status == "default":
                    emp["closed_month_index"] = i
                    break

    # ---------- employees ----------
    def _new_employee(self, emp: dict, slot: int, hire_date: date) -> dict:
        p, rng = self.p, self.rng
        smin, smax, _, female_share, rural_share = INDUSTRIES[emp["industry"]]
        smin, smax = smin * p.salary_scale, smax * p.salary_scale
        salary = int(round(np.exp(rng.uniform(np.log(smin), np.log(smax))) / 500) * 500)
        u = rng.random()
        behaviour = "abuser" if u < p.abuser_share else "chronic" if u < p.abuser_share + p.chronic_share else "normal"
        return {
            "employee_id": f"{emp['employer_id']}-{slot:04d}-{len(self.employees):05d}",
            "employer_id": emp["employer_id"],
            "salary_bdt": salary,
            "hire_date": hire_date,
            "gender": "female" if rng.random() < female_share else "male",
            "region": "rural" if rng.random() < rural_share else "urban",
            "age_band": str(rng.choice(AGE_BANDS, p=[0.30, 0.40, 0.20, 0.10])),
            "behaviour": behaviour,
            "salary_to_upay": bool(rng.random() < p.salary_to_upay_share),
            "end_date": None,
            "end_reason": None,
        }

    def _initial_hire_date(self) -> date:
        rng = self.rng
        days_back = int(rng.uniform(0, 365)) if rng.random() < 0.2 else int(rng.uniform(365, 8 * 365))
        return self.p.start - timedelta(days=days_back)

    def build_employees(self) -> None:
        for emp in self.employers:
            for slot in range(emp["headcount"]):
                person = self._new_employee(emp, slot, self._initial_hire_date())
                self.employees.append(person)
                self._simulate_slot(emp, slot, person)

    def _simulate_slot(self, emp: dict, slot: int, person: dict) -> None:
        p, rng = self.p, self.rng
        closed = emp["closed_month_index"]
        for i, ms in enumerate(self.month_starts):
            if closed is not None and i > closed:
                return
            if person["hire_date"] > ms:
                continue
            dim = calendar.monthrange(ms.year, ms.month)[1]
            tenure_days = (ms - person["hire_date"]).days
            hazard = p.attrition_short_tenure if tenure_days < 365 else p.attrition_long_tenure
            resign_day = int(rng.integers(1, dim + 1)) if rng.random() < hazard else None

            reqs = self._simulate_requests(emp, person, i, ms, dim, tenure_days, resign_day)
            if person["behaviour"] == "abuser" and reqs and resign_day is None:
                if rng.random() < p.abuser_resign_after_advance:
                    resign_day = min(dim, reqs[-1] + int(rng.integers(1, 6)))
                    person["end_reason"] = "abuse_pattern"

            active_days = resign_day if resign_day else dim
            absences = int(min(active_days, rng.poisson(1.0)))
            self.attendance.append(
                {
                    "employee_id": person["employee_id"],
                    "work_month": ms.isoformat()[:7],
                    "days_in_month": dim,
                    "active_days": active_days,
                    "earned_days": active_days - absences,
                }
            )

            if closed is not None and i == closed:
                end = self.runs[(emp["employer_id"], i)]["scheduled_date"]
                self._end(person, end, "employer_closed")
                return
            if resign_day:
                self._end(person, ms.replace(day=resign_day), person["end_reason"] or "voluntary")
                successor = self._new_employee(emp, slot, _add_months(ms, 1))
                self.employees.append(successor)
                self._simulate_slot(emp, slot, successor)
                return

    def _end(self, person: dict, end: date, reason: str) -> None:
        person["end_date"], person["end_reason"] = end, reason
        self.resignations.append(
            {"employee_id": person["employee_id"], "employer_id": person["employer_id"], "end_date": end, "reason": reason}
        )

    # ---------- requests and advances ----------
    def _request_prob(self, person: dict, ms: date, tenure_days: int) -> float:
        p = self.p
        if person["behaviour"] == "chronic":
            return p.chronic_request_prob
        if person["behaviour"] == "abuser":
            return p.abuser_request_prob
        prob = p.base_request_prob * min(2.0, np.sqrt(p.ref_salary / person["salary_bdt"]))
        if tenure_days < 365:
            prob *= p.short_tenure_multiplier
        if (ms.year, ms.month) in p.eid_months:
            prob *= p.eid_multiplier
        return min(prob, 0.9)

    def _simulate_requests(self, emp, person, i, ms, dim, tenure_days, resign_day) -> list[int]:
        p, rng, policy = self.p, self.rng, self.policy
        if rng.random() >= self._request_prob(person, ms, tenure_days):
            return []
        n = 2 if rng.random() < p.second_request_prob else 1
        last_day = (resign_day or dim + 1) - 1
        if last_day < 1:
            return []
        days = np.arange(1, last_day + 1)
        w = np.where(days >= p.late_window_start_day, p.late_window_weight, 1.0)
        req_days = sorted(int(d) for d in rng.choice(days, size=min(n, len(days)), replace=False, p=w / w.sum()))

        cap_salary = policy.cap_pct_of_salary / 100 * person["salary_bdt"]
        outstanding, approved_days = 0, []
        for count, d in enumerate(req_days, start=1):
            frac = 1.0 if person["behaviour"] == "abuser" else rng.uniform(0.3, 1.0)
            requested = max(policy.min_advance_bdt, _round_down(frac * cap_salary))
            earned = person["salary_bdt"] * d / dim
            hard_cap = _round_down(min(cap_salary, earned) - outstanding)
            decline = None
            if (ms.replace(day=d) - person["hire_date"]).days < policy.min_tenure_days:
                decline = "TENURE_TOO_SHORT"
            elif count > policy.max_advances_per_month:
                decline = "MONTHLY_COUNT_LIMIT"
            elif hard_cap < policy.min_advance_bdt:
                decline = "CAP_BELOW_MINIMUM"
            request_id = f"R{len(self.requests) + 1:06d}"
            approved = 0 if decline else min(requested, hard_cap)
            self.requests.append(
                {
                    "request_id": request_id,
                    "employee_id": person["employee_id"],
                    "employer_id": emp["employer_id"],
                    "request_date": ms.replace(day=d),
                    "work_month": ms.isoformat()[:7],
                    "amount_requested": requested,
                    "earned_to_date": int(earned),
                    "hard_cap": max(hard_cap, 0),
                    "status": "declined" if decline else "approved",
                    "decline_reason": decline,
                }
            )
            if not decline:
                outstanding += approved
                approved_days.append(d)
                self._issue_advance(emp, person, i, ms.replace(day=d), request_id, approved)
        return approved_days

    def _issue_advance(self, emp, person, i, issue_date, request_id, amount) -> None:
        policy = self.policy
        fee = int(policy.fee_flat_bdt + round(policy.fee_pct_of_amount / 100 * amount))
        run = self.runs[(emp["employer_id"], i)]
        self.advances.append(
            {
                "advance_id": f"A{len(self.advances) + 1:06d}",
                "request_id": request_id,
                "employee_id": person["employee_id"],
                "employer_id": emp["employer_id"],
                "work_month": run["work_month"],
                "month_index": i,
                "issue_date": issue_date,
                "amount": int(amount),
                "fee": fee,
                "due_date": run["scheduled_date"],
            }
        )

    # ---------- outcomes ----------
    def settle_advances(self) -> None:
        people = {e["employee_id"]: e for e in self.employees}
        for adv in self.advances:
            self._settle(adv, people[adv["employee_id"]])

    def _pay(self, adv: dict, when: date, amount: int, step: str) -> None:
        self.repayments.append(
            {
                "repayment_id": f"P{len(self.repayments) + 1:06d}",
                "advance_id": adv["advance_id"],
                "date": when,
                "amount": int(amount),
                "step": step,
            }
        )

    def _settle(self, adv: dict, person: dict) -> None:
        p, rng, policy = self.p, self.rng, self.policy
        run = self.runs[(adv["employer_id"], adv["month_index"])]
        due = adv["amount"] + adv["fee"]
        remaining, last_date, cause = due, None, None
        resigned_before = person["end_date"] is not None and person["end_date"] < run["scheduled_date"]

        # Step 1: employer bulk remittance (or final settlement for leavers)
        if run["status"] == "default":
            cause = "employer_default"
        elif resigned_before and person["end_reason"] != "employer_closed":
            if rng.random() < p.final_settlement_recovery_prob:
                self._pay(adv, run["actual_date"], remaining, "employer_remittance")
                remaining, last_date = 0, run["actual_date"]
            else:
                cause = "resigned_before_payday"
        else:
            share = run["paid_share"] if run["status"] == "partial" else 1.0
            paid = int(round(due * share))
            self._pay(adv, run["actual_date"], paid, "employer_remittance")
            remaining, last_date = due - paid, run["actual_date"]
            if remaining:
                cause = "partial_payroll"

        # Step 2: consented wallet auto-debit
        if remaining:
            prob = p.wallet_debit_prob_salary_in_upay if person["salary_to_upay"] else p.wallet_debit_prob_other
            if rng.random() < prob:
                when = run["scheduled_date"] + timedelta(days=int(rng.integers(1, policy.grace_days + 1)))
                self._pay(adv, when, remaining, "wallet_debit")
                remaining, last_date = 0, when

        # Step 3: carry-over to the next payday (only if still employed and employer still paying)
        if remaining:
            next_run = self.runs.get((adv["employer_id"], adv["month_index"] + 1))
            still_employed = person["end_date"] is None or person["end_date"] > run["scheduled_date"]
            if (
                still_employed
                and next_run
                and next_run["status"] != "default"
                and rng.random() < p.carry_over_recovery_prob
            ):
                self._pay(adv, next_run["actual_date"], remaining, "carry_over")
                remaining, last_date = 0, next_run["actual_date"]
            else:
                cause = cause or "carry_over_failed"

        # Step 4: write-off
        writeoff_date = None
        if remaining:
            writeoff_date = run["scheduled_date"] + timedelta(days=policy.writeoff_after_days)
            self._pay(adv, writeoff_date, remaining, "write_off")

        grace_end = run["scheduled_date"] + timedelta(days=policy.grace_days)
        adv.update(
            {
                "recovered_amount": due - remaining,
                "loss_amount": remaining,
                "final_step": "write_off" if remaining else self.repayments[-1]["step"],
                "recovered_by_grace": bool(remaining == 0 and last_date is not None and last_date <= grace_end),
                "settled_date": writeoff_date or last_date,
                "loss_cause": cause if remaining else None,
            }
        )


def generate(profile: str = "A", seed: int = 42, scale: float = 1.0, policy: PolicyParams | None = None) -> dict[str, pd.DataFrame]:
    """Build the synthetic world. Same profile, seed and scale always give identical tables."""
    p = PROFILES[profile]
    if scale != 1.0:
        p = replace(p, n_employers=max(2, round(p.n_employers * scale)), n_employees=max(20, round(p.n_employees * scale)))
    world = _World(p, policy or PolicyParams(), seed)
    world.build_employers()
    world.build_payroll()
    world.build_employees()
    world.settle_advances()

    test_start = _add_months(p.start, p.months - p.test_months)
    employers = pd.DataFrame(world.employers)
    return {
        "employers": employers,
        "employees": pd.DataFrame(world.employees),
        "payroll_runs": pd.DataFrame(world.runs.values()),
        "attendance": pd.DataFrame(world.attendance),
        "advance_requests": pd.DataFrame(world.requests),
        "advances": pd.DataFrame(world.advances),
        "repayments": pd.DataFrame(world.repayments),
        "resignations": pd.DataFrame(world.resignations),
        "meta": pd.DataFrame(
            [
                {
                    "profile": p.name,
                    "seed": seed,
                    "scale": scale,
                    "start": p.start,
                    "months": p.months,
                    "test_start": test_start,
                }
            ]
        ),
    }
