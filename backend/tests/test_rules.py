import calendar
from datetime import date

import numpy as np
import pytest

from app.config import PolicyParams
from app.rules import EmployeeContext, EmployerContext, HistoryContext, RequestContext, due_date_for, evaluate, fee_for

POLICY = PolicyParams()
EMPLOYER = EmployerContext("E001", payroll_day=1)
RAHIM = EmployeeContext("E001-0001-00001", salary_bdt=18_000, hire_date=date(2023, 9, 1))
OCT_25 = date(2026, 10, 25)


def run(amount=5_000, as_of=OCT_25, employer=EMPLOYER, employee=RAHIM, history=HistoryContext(), policy=POLICY, **request):
    return evaluate(employer, employee, history, RequestContext(as_of=as_of, amount_bdt=amount, **request), policy)


def test_rahim_happy_path_is_capped_at_20_percent_of_salary():
    r = run(5_000)
    assert r.eligible and r.decline_codes == []
    assert r.hard_cap_bdt == 3_600 and r.approved_bdt == 3_600
    assert r.fee_bdt == 25 and r.total_due_bdt == 3_625
    assert r.due_date == date(2026, 11, 1) and r.grace_end == date(2026, 11, 6)
    codes = [t.code for t in r.trace]
    assert "SALARY_CAP_LIMIT" in codes and "AMOUNT_REDUCED_TO_LIMIT" in codes


def test_request_within_cap_is_approved_in_full():
    r = run(2_000)
    assert r.approved_bdt == 2_000
    assert "AMOUNT_REDUCED_TO_LIMIT" not in [t.code for t in r.trace]


def test_teams_example_50k_salary_5k_on_the_25th():
    employee = EmployeeContext("X", salary_bdt=50_000, hire_date=date(2022, 1, 1))
    r = run(5_000, employee=employee)
    assert r.eligible and r.approved_bdt == 5_000 and r.hard_cap_bdt == 10_000
    assert not r.needs_human_review


def test_large_amount_needs_human_review():
    employee = EmployeeContext("X", salary_bdt=50_000, hire_date=date(2022, 1, 1))
    r = run(10_000, employee=employee)
    assert r.eligible and r.needs_human_review
    assert "LARGE_AMOUNT_REVIEW" in [t.code for t in r.trace]


def test_day_one_is_limited_by_earned_days():
    r = run(5_000, as_of=date(2026, 10, 1))
    assert r.eligible
    assert "EARNED_DAYS_LIMIT" in [t.code for t in r.trace]
    assert r.approved_bdt == 500  # 18,000 x 1/31 = 580, minus 25 fee, rounded down to 100
    low_paid = EmployeeContext("Y", salary_bdt=12_000, hire_date=date(2023, 1, 1))
    r = run(5_000, as_of=date(2026, 10, 1), employee=low_paid)
    assert not r.eligible and "CAP_BELOW_MINIMUM" in r.decline_codes
    assert r.approved_bdt == 0 and r.fee_bdt == 0


def test_absences_reduce_earned_wages():
    full = run(5_000, as_of=date(2026, 10, 10))
    absent = run(5_000, as_of=date(2026, 10, 10), earned_days=5)
    assert absent.hard_cap_bdt < full.hard_cap_bdt


def test_resigned_employee_is_declined():
    gone = EmployeeContext(RAHIM.employee_id, RAHIM.salary_bdt, RAHIM.hire_date, active=False)
    assert run(employee=gone).decline_codes == ["EMPLOYEE_NOT_ACTIVE"]


def test_kill_switch_blocks_new_advances():
    r = run(kill_switch=True)
    assert not r.eligible and r.decline_codes == ["KILL_SWITCH_ON"]


@pytest.mark.parametrize(
    "employer, code",
    [
        (EmployerContext("E001", payroll_day=1, opted_in=False), "EMPLOYER_NOT_OPTED_IN"),
        (EmployerContext("E001", payroll_day=1, closed=True), "EMPLOYER_CLOSED"),
    ],
)
def test_employer_must_be_opted_in_and_open(employer, code):
    assert run(employer=employer).decline_codes == [code]


def test_tenure_boundary():
    hired = date(2026, 7, 27)  # exactly 90 days before 25 Oct
    assert run(employee=EmployeeContext("N", 18_000, hired)).eligible
    r = run(employee=EmployeeContext("N", 18_000, date(2026, 7, 28)))
    assert r.decline_codes == ["TENURE_TOO_SHORT"]


def test_monthly_count_limit():
    assert run(history=HistoryContext(advances_this_month=1)).eligible
    assert run(history=HistoryContext(advances_this_month=2)).decline_codes == ["MONTHLY_COUNT_LIMIT"]


def test_chronic_borrower_gets_cooling_off():
    three_in_a_row = HistoryContext(months_with_advance=frozenset({"2026-07", "2026-08", "2026-09"}))
    assert run(history=three_in_a_row).decline_codes == ["COOLING_OFF"]
    two_in_a_row = HistoryContext(months_with_advance=frozenset({"2026-08", "2026-09"}))
    assert run(history=two_in_a_row).eligible
    with_gap = HistoryContext(months_with_advance=frozenset({"2026-06", "2026-07", "2026-09"}))
    assert run(history=with_gap).eligible
    year_boundary = HistoryContext(months_with_advance=frozenset({"2025-11", "2025-12", "2026-01"}))
    assert run(as_of=date(2026, 2, 25), history=year_boundary).decline_codes == ["COOLING_OFF"]


def test_outstanding_balance_reduces_the_cap():
    r = run(5_000, history=HistoryContext(advances_this_month=1, outstanding_bdt=2_000))
    assert r.hard_cap_bdt == 1_600


def test_carry_over_halves_the_salary_cap():
    r = run(5_000, history=HistoryContext(has_carry_over=True))
    assert r.hard_cap_bdt == 1_800
    assert "CARRY_OVER_REDUCTION" in [t.code for t in r.trace]


def test_amount_below_minimum_is_declined():
    assert run(300).decline_codes == ["AMOUNT_BELOW_MINIMUM"]


def test_fee_and_due_date():
    assert fee_for(3_600, POLICY) == 25
    pct_policy = PolicyParams(fee_flat_bdt=10, fee_pct_of_amount=1.5)
    assert fee_for(3_000, pct_policy) == 55
    assert due_date_for(date(2026, 12, 20), 5) == date(2027, 1, 5)


def test_amount_plus_fee_never_exceeds_earned_wages_or_salary_cap():
    rng = np.random.default_rng(0)
    for _ in range(2_000):
        policy = PolicyParams(fee_flat_bdt=int(rng.integers(0, 60)), fee_pct_of_amount=float(rng.choice([0.0, 1.0, 2.5])))
        salary = int(rng.integers(10, 150)) * 1_000
        as_of = date(2026, int(rng.integers(1, 13)), 1)
        as_of = as_of.replace(day=int(rng.integers(1, calendar.monthrange(as_of.year, as_of.month)[1] + 1)))
        owed = int(rng.integers(0, 3)) * 500
        r = run(
            int(rng.integers(500, 40_000)),
            as_of=as_of,
            employee=EmployeeContext("P", salary, date(2020, 1, 1)),
            history=HistoryContext(outstanding_bdt=owed),
            policy=policy,
        )
        earned = salary * as_of.day / calendar.monthrange(as_of.year, as_of.month)[1]
        if r.approved_bdt:
            assert r.approved_bdt + r.fee_bdt + owed <= earned + 1e-9
            assert r.approved_bdt + owed <= salary * policy.cap_pct_of_salary / 100 + 1e-9
        else:
            assert r.fee_bdt == 0 and r.total_due_bdt == 0
        assert r.hard_cap_bdt % 100 == 0  # the limit is rounded; a request inside it is honoured as asked


def test_every_trace_item_is_machine_readable():
    for r in (run(), run(kill_switch=True), run(300)):
        for item in r.trace:
            assert item.code.isupper() and item.detail and isinstance(item.values, dict)


def test_dependency_guard_halves_the_limit_for_habitual_use_with_gaps():
    # 4 of the last 6 months, never 3 in a row: cooling-off does not fire, the dependency guard does.
    habitual = HistoryContext(months_with_advance=frozenset({"2026-04", "2026-05", "2026-07", "2026-09"}))
    r = run(5_000, history=habitual)
    assert r.eligible and r.hard_cap_bdt == 1_800
    assert "DEPENDENCY_NUDGE" in [t.code for t in r.trace]
    occasional = HistoryContext(months_with_advance=frozenset({"2026-05", "2026-07", "2026-09"}))
    assert "DEPENDENCY_NUDGE" not in [t.code for t in run(5_000, history=occasional).trace]


def test_attendance_feed_lowers_earned_days():
    full = run(5_000, as_of=date(2026, 10, 10))
    absent = run(5_000, as_of=date(2026, 10, 10), earned_days=6)
    assert absent.hard_cap_bdt < full.hard_cap_bdt
    assert "ATTENDANCE_ADJUSTED" in [t.code for t in absent.trace]
