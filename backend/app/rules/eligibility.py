"""Hard rules for an advance request: eligibility, hard cap, fee and repayment date.

Pure functions with no database access. ML tiers (F08+) only ever shrink the cap computed here.
Every rule adds a machine-readable TraceItem so each decision can be explained.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.config import PolicyParams


@dataclass(frozen=True)
class EmployerContext:
    employer_id: str
    payroll_day: int
    opted_in: bool = True
    closed: bool = False


@dataclass(frozen=True)
class EmployeeContext:
    employee_id: str
    salary_bdt: int
    hire_date: date
    active: bool = True  # False once the employee has resigned or been let go


@dataclass(frozen=True)
class HistoryContext:
    advances_this_month: int = 0
    outstanding_bdt: int = 0  # unrecovered principal across all open advances
    months_with_advance: frozenset[str] = frozenset()  # work months ("YYYY-MM") with at least one advance
    has_carry_over: bool = False  # an earlier advance was not fully recovered on its payday


@dataclass(frozen=True)
class RequestContext:
    as_of: date
    amount_bdt: int
    earned_days: int | None = None  # days worked so far this month; defaults to calendar days elapsed
    kill_switch: bool = False


@dataclass(frozen=True)
class TraceItem:
    code: str
    passed: bool
    detail: str
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RuleResult:
    eligible: bool
    requested_bdt: int
    hard_cap_bdt: int
    approved_bdt: int
    fee_bdt: int
    total_due_bdt: int
    due_date: date
    grace_end: date
    needs_human_review: bool
    trace: list[TraceItem]

    @property
    def decline_codes(self) -> list[str]:
        return [t.code for t in self.trace if not t.passed]


def _round_down(x: Decimal | int, step: int = 100) -> int:
    return int(Decimal(x) // step * step)


def fee_for(amount_bdt: int, policy: PolicyParams) -> int:
    pct = Decimal(str(policy.fee_pct_of_amount)) / 100 * amount_bdt
    return policy.fee_flat_bdt + int(pct.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def due_date_for(as_of: date, payroll_day: int) -> date:
    """Wages for the month of `as_of` are paid on `payroll_day` of the next month."""
    year, month = (as_of.year + 1, 1) if as_of.month == 12 else (as_of.year, as_of.month + 1)
    return date(year, month, payroll_day)


def consecutive_months_before(as_of: date, months_with_advance: frozenset[str]) -> int:
    """How many months in a row, ending last month, had an advance."""
    count, year, month = 0, as_of.year, as_of.month
    while True:
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        if f"{year}-{month:02d}" not in months_with_advance:
            return count
        count += 1


def months_with_advance_in_window(as_of: date, months_with_advance: frozenset[str], lookback: int) -> int:
    """How many of the `lookback` months before this one had an advance (gaps allowed)."""
    count, year, month = 0, as_of.year, as_of.month
    for _ in range(lookback):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        count += f"{year}-{month:02d}" in months_with_advance
    return count


def evaluate(
    employer: EmployerContext,
    employee: EmployeeContext,
    history: HistoryContext,
    request: RequestContext,
    policy: PolicyParams,
) -> RuleResult:
    trace: list[TraceItem] = []

    def check(code_ok: str, code_fail: str, ok: bool, detail: str, **values) -> bool:
        trace.append(TraceItem(code_ok if ok else code_fail, ok, detail, values))
        return ok

    as_of = request.as_of
    check("SERVICE_ON", "KILL_SWITCH_ON", not request.kill_switch, "New advances are paused by operations" if request.kill_switch else "Service is running")
    check("EMPLOYER_OPTED_IN", "EMPLOYER_NOT_OPTED_IN", employer.opted_in, "Employer has joined the programme" if employer.opted_in else "Employer has not joined the programme")
    check("EMPLOYER_OPEN", "EMPLOYER_CLOSED", not employer.closed, "Employer is paying wages" if not employer.closed else "Employer has stopped paying wages")
    check("EMPLOYEE_ACTIVE", "EMPLOYEE_NOT_ACTIVE", employee.active, "Employee is on payroll" if employee.active else "Employee is no longer on payroll")

    check(
        "IN_ADVANCE_WINDOW",
        "TOO_EARLY_IN_MONTH",
        as_of.day >= policy.advance_window_start_day,
        f"Advances open on day {policy.advance_window_start_day} of the month (today is day {as_of.day})",
        day=as_of.day,
        opens_on_day=policy.advance_window_start_day,
    )
    tenure = (as_of - employee.hire_date).days
    check(
        "TENURE_OK",
        "TENURE_TOO_SHORT",
        tenure >= policy.min_tenure_days,
        f"Tenure {tenure} days (minimum {policy.min_tenure_days})",
        tenure_days=tenure,
        min_tenure_days=policy.min_tenure_days,
    )
    check(
        "MONTHLY_COUNT_OK",
        "MONTHLY_COUNT_LIMIT",
        history.advances_this_month < policy.max_advances_per_month,
        f"{history.advances_this_month} of {policy.max_advances_per_month} advances used this month",
        used=history.advances_this_month,
        limit=policy.max_advances_per_month,
    )
    streak = consecutive_months_before(as_of, history.months_with_advance)
    check(
        "NO_COOLING_OFF",
        "COOLING_OFF",
        streak < policy.cooling_off_consecutive_months,
        f"Advances in {streak} consecutive month(s) before this one (pause after {policy.cooling_off_consecutive_months})",
        consecutive_months=streak,
        limit=policy.cooling_off_consecutive_months,
    )

    # Hard cap: the smaller of the salary cap and wages already earned, minus what is still owed.
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    earned_days = as_of.day if request.earned_days is None else min(request.earned_days, as_of.day)
    earned = Decimal(employee.salary_bdt) * earned_days / days_in_month
    salary_cap = Decimal(employee.salary_bdt) * Decimal(str(policy.cap_pct_of_salary)) / 100
    if history.has_carry_over:
        salary_cap = salary_cap * (100 - Decimal(str(policy.carry_over_limit_reduction_pct))) / 100
        trace.append(
            TraceItem(
                "CARRY_OVER_REDUCTION",
                True,
                f"Limit reduced by {policy.carry_over_limit_reduction_pct:g}% because an earlier advance carried over",
                {"reduction_pct": policy.carry_over_limit_reduction_pct},
            )
        )
    # Dependency guard (Phase 2): habitual use with gaps escapes the consecutive-month cooling-off,
    # so frequent users get a smaller limit and a savings nudge instead of being encouraged to borrow every month.
    used_months = months_with_advance_in_window(as_of, history.months_with_advance, policy.dependency_lookback_months)
    if used_months >= policy.dependency_months_threshold:
        salary_cap = salary_cap * (100 - Decimal(str(policy.dependency_limit_reduction_pct))) / 100
        trace.append(
            TraceItem(
                "DEPENDENCY_NUDGE",
                True,
                f"Advances in {used_months} of the last {policy.dependency_lookback_months} months: limit reduced by "
                f"{policy.dependency_limit_reduction_pct:g}% and a savings plan is suggested",
                {"months_used": used_months, "lookback_months": policy.dependency_lookback_months, "reduction_pct": policy.dependency_limit_reduction_pct},
            )
        )
    if request.earned_days is not None:
        trace.append(
            TraceItem(
                "ATTENDANCE_ADJUSTED",
                True,
                f"Earned days come from the employer's attendance feed: {earned_days} of {as_of.day} days so far",
                {"earned_days": earned_days, "calendar_days": as_of.day},
            )
        )
    # Amount + fee must fit inside earned wages so the payday deduction never exceeds them.
    pct = Decimal(str(policy.fee_pct_of_amount)) / 100
    earned_room = (earned - history.outstanding_bdt - policy.fee_flat_bdt) / (1 + pct)
    salary_room = salary_cap - history.outstanding_bdt
    hard_cap = max(0, _round_down(min(earned_room, salary_room)))
    binding = "EARNED_DAYS_LIMIT" if earned_room <= salary_room else "SALARY_CAP_LIMIT"
    trace.append(
        TraceItem(
            binding,
            True,
            f"Limit {hard_cap} BDT: {earned_days} of {days_in_month} days earned, cap {policy.cap_pct_of_salary:g}% of salary, {history.outstanding_bdt} BDT still owed",
            {
                "hard_cap_bdt": hard_cap,
                "earned_to_date_bdt": int(earned),
                "salary_cap_bdt": int(salary_cap),
                "outstanding_bdt": history.outstanding_bdt,
            },
        )
    )
    check(
        "CAP_ABOVE_MINIMUM",
        "CAP_BELOW_MINIMUM",
        hard_cap >= policy.min_advance_bdt,
        f"Available {hard_cap} BDT (minimum advance {policy.min_advance_bdt})",
        hard_cap_bdt=hard_cap,
        min_advance_bdt=policy.min_advance_bdt,
    )
    check(
        "AMOUNT_ABOVE_MINIMUM",
        "AMOUNT_BELOW_MINIMUM",
        request.amount_bdt >= policy.min_advance_bdt,
        f"Requested {request.amount_bdt} BDT (minimum {policy.min_advance_bdt})",
        requested_bdt=request.amount_bdt,
    )

    eligible = all(t.passed for t in trace)
    approved = min(request.amount_bdt, hard_cap) if eligible else 0
    if eligible and approved < request.amount_bdt:
        trace.append(
            TraceItem("AMOUNT_REDUCED_TO_LIMIT", True, f"Requested {request.amount_bdt} BDT, offered {approved} BDT", {"offered_bdt": approved})
        )
    fee = fee_for(approved, policy) if approved else 0
    due = due_date_for(as_of, employer.payroll_day)
    needs_review = eligible and approved >= policy.large_amount_threshold_bdt
    if needs_review:
        trace.append(
            TraceItem(
                "LARGE_AMOUNT_REVIEW",
                True,
                f"{approved} BDT is at or above {policy.large_amount_threshold_bdt} BDT, so a person must approve it",
                {"threshold_bdt": policy.large_amount_threshold_bdt},
            )
        )
    return RuleResult(
        eligible=eligible,
        requested_bdt=request.amount_bdt,
        hard_cap_bdt=hard_cap,
        approved_bdt=approved,
        fee_bdt=fee,
        total_due_bdt=approved + fee,
        due_date=due,
        grace_end=due + timedelta(days=policy.grace_days),
        needs_human_review=needs_review,
        trace=trace,
    )
