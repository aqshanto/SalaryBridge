"""Advance offer decision: rules -> M1 -> M2 tier -> M3 downgrade -> M5 flags -> offer.

Order of authority:
1. Rules decide eligibility and the hard cap. A failed rule declines (fixed policy).
2. Models can only shrink the cap or send the request to a person. A model never declines by itself.
3. The LLM (F16) only words the reasons below; it is not called here.

Features are built with the same functions used in training (app/ml/*), from the seed history plus
this session's advances, so training and live decisions see the same definitions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.config import PolicyParams
from app.ml import m1_employer as m1
from app.ml import m2_repayment as m2
from app.ml import m3_attrition as m3
from app.ml import m5_abuse as m5
from app.rules import EmployeeContext, EmployerContext, HistoryContext, RequestContext, evaluate, fee_for
from app.rules.abuse import AbuseFlags, route
from app.rules.tiers import apply_attrition, tier_for, tiered_amount
from app.services.capital import forecast as capital_forecast
from app.sim.session import SimSession, sim_decisions

CARRY_OVER_LOOKBACK_DAYS = 30
PREVIEW_ID = "PREVIEW"
INFORMATIVE_RULE_CODES = {"SALARY_CAP_LIMIT", "EARNED_DAYS_LIMIT", "CARRY_OVER_REDUCTION", "AMOUNT_REDUCED_TO_LIMIT", "LARGE_AMOUNT_REVIEW"}


class DecisionError(ValueError):
    pass


@dataclass
class Reason:
    code: str
    source: str  # rule | m1 | m2 | m3 | m5 | tier
    direction: str  # raises_risk | lowers_risk | limit | decline | review | info
    detail: str = ""


@dataclass
class Decision:
    decision_id: str
    status: str  # offered | queued | declined
    employee_id: str
    employer_id: str
    sim_date: str
    requested_bdt: int
    hard_cap_bdt: int
    max_amount_bdt: int
    approved_amount_bdt: int
    fee_bdt: int
    total_due_bdt: int
    repayment_date: str
    grace_end: str
    tier: str | None
    needs_human: bool
    risk: dict
    reasons: list[Reason] = field(default_factory=list)
    model_versions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["reasons"] = [asdict(r) for r in self.reasons]
        return out


def _iso(x) -> str:
    return pd.Timestamp(x).date().isoformat()


def _employee_history(session: SimSession, employee_id: str) -> pd.DataFrame:
    """Seed advances plus this session's advances for one person, in the training schema."""
    seed = session.world.advances[session.world.advances["employee_id"] == employee_id]
    seed = seed.merge(
        session.world.requests[["request_id", "hard_cap", "amount_requested"]], on="request_id", how="left"
    )[["advance_id", "employee_id", "employer_id", "work_month", "issue_date", "due_date", "amount", "amount_requested", "hard_cap", "recovered_by_grace", "settled_date"]]
    live = session.advances_df(employee_id)
    if not live.empty:
        live = live[seed.columns.tolist()]
        seed = pd.concat([seed, live], ignore_index=True)
    for col in ("issue_date", "due_date", "settled_date"):
        seed[col] = pd.to_datetime(seed[col])
    return seed


def _history_context(history: pd.DataFrame, live: pd.DataFrame, as_of: date) -> HistoryContext:
    if history.empty:
        return HistoryContext()
    settled = pd.to_datetime(history["settled_date"])
    still_owed = settled.isna() | (settled > pd.Timestamp(as_of))
    failed = history["recovered_by_grace"].notna() & ~history["recovered_by_grace"].astype("boolean").fillna(True)
    recent_failure = failed & (settled.isna() | (settled >= pd.Timestamp(as_of - timedelta(days=CARRY_OVER_LOOKBACK_DAYS))))
    this_month = as_of.isoformat()[:7]
    return HistoryContext(
        advances_this_month=int((live["work_month"] == this_month).sum()) if not live.empty else 0,
        outstanding_bdt=int(history.loc[still_owed, "amount"].sum()),
        months_with_advance=frozenset(history["work_month"].astype(str)),
        has_carry_over=bool(recent_failure.any()),
    )


def _employer_runs(session: SimSession, employer_id: str) -> pd.DataFrame:
    seed = session.world.payroll_runs[session.world.payroll_runs["employer_id"] == employer_id]
    cols = ["employer_id", "scheduled_date", "actual_date", "status", "delay_days", "paid_share"]
    live = session.payroll_runs_df(employer_id)
    runs = pd.concat([seed[cols], live[cols]], ignore_index=True) if not live.empty else seed[cols].copy()
    # Seed dates come back from SQLite as text, live ones as date objects: use one type.
    for col in ("scheduled_date", "actual_date"):
        runs[col] = pd.to_datetime(runs[col])
    return runs


def _requests_history(session: SimSession, employee_id: str, history: pd.DataFrame) -> pd.DataFrame:
    seed = session.world.requests[session.world.requests["employee_id"] == employee_id]
    live = session.advances_df(employee_id)
    if live.empty:
        return seed
    live_req = pd.DataFrame(
        {
            "request_id": live["decision_id"],
            "employee_id": live["employee_id"],
            "employer_id": live["employer_id"],
            "request_date": live["issue_date"],
            "work_month": live["work_month"],
            "amount_requested": live["amount_requested"],
            "hard_cap": live["hard_cap"],
            "status": "approved",
        }
    )
    return pd.concat([seed, live_req], ignore_index=True)


def decide(session: SimSession, employee_id: str, amount_bdt: int, policy: PolicyParams, preview: bool = False) -> Decision:
    """Run the full decision. preview=True runs the same pipeline without logging it (for 'you can get up to')."""
    world = session.world
    if employee_id not in world.employees.index:
        raise DecisionError(f"Unknown or inactive employee: {employee_id}")
    if amount_bdt <= 0:
        raise DecisionError("amount_bdt must be positive")
    person = world.employees.loc[employee_id]
    employer = world.employers_all.set_index("employer_id", drop=False).loc[person["employer_id"]]
    as_of = session.sim_date
    closed = person["employer_id"] in session.closed_employers()
    left = session.resignation(employee_id)

    history = _employee_history(session, employee_id)
    live = session.advances_df(employee_id)
    history_ctx = _history_context(history, live, as_of)
    rules = evaluate(
        EmployerContext(employer["employer_id"], int(employer["payroll_day"]), bool(employer["opted_in"]), closed),
        EmployeeContext(employee_id, int(person["salary_bdt"]), pd.Timestamp(person["hire_date"]).date(), left is None or left > as_of),
        history_ctx,
        RequestContext(as_of=as_of, amount_bdt=int(amount_bdt), kill_switch=session.kill_switch),
        policy,
    )

    reasons: list[Reason] = []
    for t in rules.trace:
        if not t.passed:
            reasons.append(Reason(t.code, "rule", "decline", t.detail))
        elif t.code in INFORMATIVE_RULE_CODES:
            reasons.append(Reason(t.code, "rule", "limit" if t.code != "LARGE_AMOUNT_REVIEW" else "review", t.detail))

    base = {
        "employee_id": employee_id,
        "employer_id": employer["employer_id"],
        "sim_date": as_of.isoformat(),
        "requested_bdt": int(amount_bdt),
        "hard_cap_bdt": rules.hard_cap_bdt,
        "repayment_date": rules.due_date.isoformat(),
        "grace_end": rules.grace_end.isoformat(),
    }
    inputs: dict = {"rule_result": {k: v for k, v in asdict(rules).items() if k != "trace"}, "rule_trace": [asdict(t) for t in rules.trace]}

    if not rules.eligible:
        if "CAP_BELOW_MINIMUM" in rules.decline_codes and history_ctx.outstanding_bdt > 0:
            # Same rule code, different story for the person: the limit is used up, not "not earned yet".
            reasons.append(Reason("LIMIT_ALREADY_USED", "rule", "info", f"{history_ctx.outstanding_bdt} BDT from an earlier advance is still owed"))
        if "COOLING_OFF" in rules.decline_codes:
            reasons.append(Reason("CHRONIC_BORROWING", "rule", "info", "Advances in several months in a row: a one-month pause protects the borrower"))
        decision = Decision(
            decision_id=PREVIEW_ID if preview else session.next_id(sim_decisions, "D"),
            status="declined",
            max_amount_bdt=0,
            approved_amount_bdt=0,
            fee_bdt=0,
            total_due_bdt=0,
            tier=None,
            needs_human=False,
            risk={},
            reasons=reasons,
            **base,
        )
        return decision if preview else _log(session, decision, inputs)

    # --- M1: employer payroll risk on the decision date
    m1_model, m2_model, m3_model, m5_model = m1.load_model(), m2.load_model(), m3.load_model(), m5.load_model()
    runs = _employer_runs(session, employer["employer_id"])
    m1_row = pd.DataFrame([m1.employer_features(employer, runs, as_of, policy.grace_days)])
    p_late = float(m1_model.predict_proba(m1_row)[0])
    for r in m1_model.reasons(m1_row, top=2)[0]:
        reasons.append(Reason(r["code"], "m1", r["direction"]))

    # --- M2 / M3: candidate advance appended to the person's history
    candidate_amount = min(int(amount_bdt), rules.hard_cap_bdt)
    cand = pd.DataFrame(
        [
            {
                "advance_id": "CANDIDATE",
                "employee_id": employee_id,
                "employer_id": employer["employer_id"],
                "work_month": as_of.isoformat()[:7],
                "issue_date": as_of,
                "due_date": rules.due_date,
                "amount": candidate_amount,
                "amount_requested": int(amount_bdt),
                "hard_cap": rules.hard_cap_bdt,
                "recovered_by_grace": np.nan,
                "settled_date": None,
            }
        ]
    )
    rows = pd.concat([history, cand], ignore_index=True) if not history.empty else cand
    rows["employer_prob_late"] = p_late
    feats = m2.compute_features(rows, world.employees_all, world.employers_all, policy.grace_days)
    feats = feats[feats["advance_id"] == "CANDIDATE"].reset_index(drop=True)
    feats["hard_cap"], feats["amount_requested"] = rules.hard_cap_bdt, int(amount_bdt)
    feats = m3.add_request_features(feats)

    p_fail = float(m2_model.predict_proba(feats)[0])
    p_leave = float(m3_model.predict_proba(feats)[0])
    tier = tier_for(p_fail, policy)
    reasons.append(Reason(f"TIER_{tier.tier}", "tier", "info", f"Repayment risk {p_fail:.1%}"))
    for r in m2_model.reasons(feats, top=3)[0]:
        reasons.append(Reason(r["code"], "m2", r["direction"]))
    tier, downgraded = apply_attrition(tier, p_leave, policy)
    if downgraded:
        reasons.append(Reason("ATTRITION_DOWNGRADE", "m3", "raises_risk", f"Chance of leaving before payday {p_leave:.1%}; tier lowered to {tier.tier}"))
        for r in m3_model.reasons(feats, top=2)[0]:
            reasons.append(Reason(r["code"], "m3", r["direction"]))

    # --- M5: unusual borrowing pattern
    req_hist = _requests_history(session, employee_id, history)
    m5_rows = m5.behaviour_features(req_hist, world.employees_all, as_of + timedelta(days=1))
    if m5_rows.empty:
        flags = AbuseFlags(anomaly_score=0.0, unusual_pattern=False, chronic=False)
    else:
        flags = m5.abuse_check(m5_rows, policy, m5_model)[0]
    tier, abuse_codes = route(tier, flags)
    for code in abuse_codes:
        reasons.append(Reason(code, "m5", "review" if code == "UNUSUAL_BORROWING_PATTERN" else "info"))

    # --- Offer
    share = tier.share_of_cap if tier.share_of_cap > 0 else policy.tier_c_share_of_cap
    max_amount = int(rules.hard_cap_bdt * share) // 100 * 100
    proposed = min(int(amount_bdt), max_amount)
    auto_amount = tiered_amount(rules.hard_cap_bdt, int(amount_bdt), tier, policy)
    needs_human = tier.needs_human or rules.needs_human_review
    if needs_human:
        status, approved = "queued", (proposed if proposed >= policy.min_advance_bdt else policy.min_advance_bdt)
    elif auto_amount == 0:
        status, approved, needs_human = "queued", policy.min_advance_bdt, True
        reasons.append(Reason("LIMIT_BELOW_MINIMUM_AFTER_RISK", "tier", "review", "Risk-adjusted limit is under the minimum; a person will decide"))
    else:
        status, approved = "offered", auto_amount
    if approved < int(amount_bdt) and status == "offered" and not any(r.code == "AMOUNT_REDUCED_TO_LIMIT" for r in reasons):
        reasons.append(Reason("AMOUNT_REDUCED_TO_LIMIT", "tier", "limit", f"Requested {amount_bdt} BDT, offered {approved} BDT"))

    # --- M4: is there enough money in the pool for this month's forecast need?
    capital = capital_forecast(session)
    if status == "offered" and (capital["pool_below_required"] or approved > capital["pool_bdt"]):
        status, needs_human = "queued", True
        reasons.append(
            Reason(
                "POOL_BELOW_FORECAST",
                "m4",
                "review",
                f"Pool {capital['pool_bdt']} BDT is below this month's forecast need {capital['current_required_pool_bdt']} BDT (P90 + buffer)",
            )
        )
    fee = fee_for(approved, policy)

    decision = Decision(
        decision_id=PREVIEW_ID if preview else session.next_id(sim_decisions, "D"),
        status=status,
        max_amount_bdt=max_amount,
        approved_amount_bdt=approved,
        fee_bdt=fee,
        total_due_bdt=approved + fee,
        tier=tier.tier,
        needs_human=needs_human,
        risk={
            "employer_prob_late": round(p_late, 4),
            "prob_fail": round(p_fail, 4),
            "prob_leave": round(p_leave, 4),
            "anomaly_score": flags.anomaly_score,
            "unusual_pattern": flags.unusual_pattern,
            "pool_bdt": capital["pool_bdt"],
            "required_pool_bdt": capital["current_required_pool_bdt"],
        },
        reasons=reasons,
        model_versions={"m1": m1_model.version, "m2": m2_model.version, "m3": m3_model.version, "m4": capital["model_version"], "m5": m5_model.version},
        **base,
    )
    inputs.update(
        {
            "m1_features": m1_row.iloc[0].to_dict(),
            "m2_m3_features": {k: v for k, v in feats.iloc[0].to_dict().items() if k in set(m2.FEATURES) | set(m3.FEATURES)},
            "m5_features": m5_rows.iloc[0].to_dict() if not m5_rows.empty else None,
        }
    )
    return decision if preview else _log(session, decision, inputs)


def _log(session: SimSession, decision: Decision, inputs: dict) -> Decision:
    payload = json.dumps({"inputs": inputs, "output": decision.to_dict()}, default=str)
    session.log_decision(
        {
            "decision_id": decision.decision_id,
            "sim_date": date.fromisoformat(decision.sim_date),
            "employee_id": decision.employee_id,
            "requested_bdt": decision.requested_bdt,
            "status": decision.status,
            "approved_bdt": decision.approved_amount_bdt,
            "payload": payload,
        }
    )
    return decision
