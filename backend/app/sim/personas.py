"""Pick the plot.md demo personas from the seed world, deterministically.

Latent labels (behaviour, reliability_type) are used here only to cast the demo characters; they are
never model inputs. Names are fictional and attached only in the demo layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import pandas as pd

from app.config import get_settings
from app.ml import m1_employer as m1
from app.rules.eligibility import consecutive_months_before
from app.sim.world import SeedWorld, load_world


SHAPLA_MAX_EMPLOYER_RISK = 0.36  # m1-v2 separates employers more sharply; no employer sits between 0.10 and 0.30
SHAPLA_MIN_EMPLOYER_RISK = 0.10  # below this the employer would not look risky in the demo


@dataclass(frozen=True)
class Persona:
    key: str
    name: str
    story: str
    employee_id: str
    employer_id: str
    industry: str
    salary_bdt: int
    hire_date: str


def _tenure(df: pd.DataFrame, as_of: date) -> pd.Series:
    return (pd.Timestamp(as_of) - pd.to_datetime(df["hire_date"])).dt.days


def _pick(candidates: pd.DataFrame, target_salary: int) -> pd.Series:
    if candidates.empty:
        raise LookupError("No employee matches this persona")
    ranked = candidates.assign(gap=(candidates["salary_bdt"] - target_salary).abs()).sort_values(["gap", "employee_id"])
    return ranked.iloc[0]


def find_personas(world: SeedWorld, start: date) -> list[Persona]:
    emp = world.employees.reset_index(drop=True).merge(
        world.employers.reset_index(drop=True)[["employer_id", "industry", "reliability_type"]], on="employer_id"
    )
    emp = emp.assign(tenure=_tenure(emp, start))
    months = world.advances.groupby("employee_id")["work_month"].agg(frozenset)
    streak = emp["employee_id"].map(lambda e: consecutive_months_before(start, months.get(e, frozenset())))
    recent_any = streak > 0
    out = []

    # Rahim: garment worker ~18,000 BDT, ~3 years, reliable employer, no recent advances.
    rahim = _pick(
        emp[(emp["industry"] == "garments") & (emp["reliability_type"] == "on_time") & emp["tenure"].between(730, 1460) & (emp["behaviour"] == "normal") & ~recent_any],
        18_000,
    )
    out.append(Persona("rahim", "Rahim", "Garment worker, reliable employer, needs cash for a sick child", rahim["employee_id"], rahim["employer_id"], rahim["industry"], int(rahim["salary_bdt"]), str(rahim["hire_date"])[:10]))

    # Karim: chronic borrower with advances in each of the last 3+ months.
    chronic = emp[(emp["behaviour"] == "chronic") & (streak >= get_settings().policy.cooling_off_consecutive_months)]
    retail = chronic[chronic["industry"] == "retail"]
    karim = _pick(retail if not retail.empty else chronic, 22_000)

    # Shapla: newer staff at an employer the model rates as risky but not the worst (highest M1 risk
    # at or below SHAPLA_MAX_EMPLOYER_RISK), so the demo shows a smaller offer rather than a queue.
    model = m1.load_model()
    risk = {}
    for eid, employer in world.employers.iterrows():
        runs = world.payroll_runs[world.payroll_runs["employer_id"] == eid]
        risk[eid] = float(model.predict_proba(pd.DataFrame([m1.employer_features(employer, runs, start, get_settings().policy.grace_days)]))[0])
    risky = sorted((e for e in risk if risk[e] <= SHAPLA_MAX_EMPLOYER_RISK), key=lambda e: (-risk[e], e))
    shapla = None
    # Prefer a genuinely risky employer; widen the tenure window before falling back to a low-risk one.
    for lo, hi, min_risk in ((100, 240, SHAPLA_MIN_EMPLOYER_RISK), (60, 400, SHAPLA_MIN_EMPLOYER_RISK), (100, 240, 0.0)):
        for employer_id in risky:
            if risk[employer_id] < min_risk:
                break
            if employer_id in (rahim["employer_id"], karim["employer_id"]):
                continue  # each demo person works for a different employer
            pool = emp[(emp["employer_id"] == employer_id) & emp["tenure"].between(lo, hi) & (emp["behaviour"] == "normal") & ~recent_any]
            if not pool.empty:
                shapla = _pick(pool, 50_000)
                break
        if shapla is not None:
            break
    out.append(Persona("shapla", "Shapla", "Newer staff at an employer that often pays late", shapla["employee_id"], shapla["employer_id"], shapla["industry"], int(shapla["salary_bdt"]), str(shapla["hire_date"])[:10]))

    out.append(Persona("karim", "Karim", "Has taken an advance every month for months", karim["employee_id"], karim["employer_id"], karim["industry"], int(karim["salary_bdt"]), str(karim["hire_date"])[:10]))
    return out


@lru_cache(maxsize=4)
def personas_for(database_url: str, start: date) -> tuple[Persona, ...]:
    return tuple(find_personas(load_world(database_url), start))
