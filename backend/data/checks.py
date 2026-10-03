"""Summaries and pattern checks shared by tests and the data report."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data.generator import ProfileParams


@dataclass
class Check:
    name: str
    detail: str
    passed: bool


def _rate(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.reindex(denominator.index, fill_value=0) / denominator


def request_rate_by_day(tables: dict) -> tuple[float, float]:
    """Requests per calendar day for days 1–5 and days 25+ (averaged over distinct days)."""
    day = pd.to_datetime(tables["advance_requests"]["request_date"]).dt.day
    early = (day <= 5).sum() / max(1, day[day <= 5].nunique())
    late = (day >= 25).sum() / max(1, day[day >= 25].nunique())
    return float(early), float(late)


def late_share_by_type(tables: dict) -> pd.Series:
    runs = tables["payroll_runs"].merge(tables["employers"][["employer_id", "reliability_type"]], on="employer_id")
    return runs["status"].isin(["late", "partial"]).groupby(runs["reliability_type"]).mean()


def lateness_persistence(tables: dict) -> tuple[float, float]:
    runs = tables["payroll_runs"].sort_values(["employer_id", "month_index"]).copy()
    runs["late"] = runs["status"].isin(["late", "partial"])
    runs["prev_late"] = runs.groupby("employer_id")["late"].shift()
    runs = runs.dropna(subset=["prev_late"])
    prev = runs["prev_late"].astype(bool)
    return float(runs.loc[prev, "late"].mean()), float(runs.loc[~prev, "late"].mean())


def eid_request_rates(tables: dict, profile: ProfileParams) -> tuple[float, float]:
    eid = {f"{y}-{m:02d}" for y, m in profile.eid_months}
    rate = _rate(tables["advance_requests"].groupby("work_month").size(), tables["attendance"].groupby("work_month").size())
    return float(rate[rate.index.isin(eid)].mean()), float(rate[~rate.index.isin(eid)].mean())


def request_month_share_by_behaviour(tables: dict) -> pd.Series:
    months = tables["advance_requests"].groupby("employee_id")["work_month"].nunique()
    share = _rate(months, tables["attendance"].groupby("employee_id").size())
    behaviour = tables["employees"].set_index("employee_id")["behaviour"]
    return share.groupby(behaviour.reindex(share.index)).mean()


def voluntary_exit_by_tenure(tables: dict, profile: ProfileParams) -> tuple[float, float]:
    emp = tables["employees"]
    voluntary = emp["end_reason"].eq("voluntary")
    short = pd.to_datetime(emp["hire_date"]) > pd.Timestamp(profile.start) - pd.Timedelta(days=365)
    return float(voluntary[short].mean()), float(voluntary[~short].mean())


def outcome_summary(tables: dict) -> dict:
    adv = tables["advances"]
    due = adv["amount"] + adv["fee"]
    return {
        "advances": len(adv),
        "approval_rate": float((tables["advance_requests"]["status"] == "approved").mean()),
        "failure_rate": float(1 - adv["recovered_by_grace"].mean()),
        "loss_rate_by_amount": float(adv["loss_amount"].sum() / due.sum()),
        "loss_causes": adv.loc[adv["loss_amount"] > 0, "loss_cause"].value_counts().to_dict(),
        "final_steps": adv["final_step"].value_counts().to_dict(),
    }


def pattern_checks(tables: dict, profile: ProfileParams) -> list[Check]:
    early, late = request_rate_by_day(tables)
    by_type = late_share_by_type(tables)
    after_late, after_ok = lateness_persistence(tables)
    eid, non_eid = eid_request_rates(tables, profile)
    behaviour = request_month_share_by_behaviour(tables)
    short, long_ = voluntary_exit_by_tenure(tables, profile)
    adv = tables["advances"]
    lost = adv[adv["loss_amount"] > 0]
    allowed_causes = {"employer_default", "resigned_before_payday", "partial_payroll", "carry_over_failed"}
    return [
        Check("Month-end surge", f"day 25+ {late:.0f}/day vs day 1–5 {early:.0f}/day", late > 2 * early),
        Check(
            "Lateness follows employer type",
            ", ".join(f"{t} {by_type.get(t, 0):.0%}" for t in ("on_time", "sometimes_late", "often_late")),
            by_type["often_late"] > by_type["sometimes_late"] > by_type["on_time"],
        ),
        Check("Lateness persists", f"after a late month {after_late:.0%}, otherwise {after_ok:.0%}", after_late > after_ok),
        Check("Eid surge", f"Eid {eid:.3f} vs other {non_eid:.3f} requests per active person", eid > 1.3 * non_eid),
        Check(
            "Chronic borrowers request most months",
            f"chronic {behaviour.get('chronic', 0):.0%} vs normal {behaviour.get('normal', 0):.0%} of active months",
            behaviour.get("chronic", 0) > 5 * behaviour.get("normal", 1),
        ),
        Check("Short tenure leaves more", f"tenure < 1y {short:.0%} vs {long_:.0%}", short > long_),
        Check(
            "Losses only from modelled causes",
            f"{len(lost)} advances with loss",
            len(lost) > 0 and set(lost["loss_cause"]) <= allowed_causes,
        ),
        Check(
            "Money adds up",
            "recovered + loss = amount + fee for every advance",
            bool((adv["recovered_amount"] + adv["loss_amount"] == adv["amount"] + adv["fee"]).all()),
        ),
    ]
