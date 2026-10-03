"""Ops dashboard numbers: KPIs and the population-wide borrowing-pattern monitor (M5)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select

from app.ml import m5_abuse as m5
from app.services.capital import forecast
from app.sim import settlement
from app.sim.session import SimSession, sim_advances, sim_decisions, sim_reviews

MONITOR_TOP = 12


def kpis(session: SimSession) -> dict:
    capital = forecast(session)
    summary = settlement.summary(session)
    with session.engine.connect() as conn:
        by_status = dict(conn.execute(select(sim_decisions.c.status, func.count()).group_by(sim_decisions.c.status)).all())
        approved_by_ops = conn.execute(select(func.count()).select_from(sim_reviews).where(sim_reviews.c.action == "approved")).scalar_one()
        queued_open = conn.execute(
            select(func.count()).select_from(sim_decisions).where(
                sim_decisions.c.status == "queued", sim_decisions.c.decision_id.not_in(select(sim_reviews.c.decision_id))
            )
        ).scalar_one()
        disbursed = conn.execute(select(func.coalesce(func.sum(sim_advances.c.amount), 0))).scalar_one()
    decisions = sum(by_status.values())
    approved = by_status.get("offered", 0) + approved_by_ops
    return {
        "outstanding_bdt": summary["outstanding_bdt"],
        "pool_bdt": capital["pool_bdt"],
        "required_pool_bdt": capital["current_required_pool_bdt"],
        "pool_below_required": capital["pool_below_required"],
        "disbursed_bdt": int(disbursed),
        "fees_income_bdt": summary["fees_income_bdt"],
        "loss_provision_bdt": summary["loss_provision_bdt"],
        "loss_rate": round(summary["loss_provision_bdt"] / disbursed, 4) if disbursed else None,
        "decisions": decisions,
        "decisions_by_status": by_status,
        "approval_rate": round(approved / decisions, 4) if decisions else None,
        "queue_open": int(queued_open),
        "kill_switch": session.kill_switch,
    }


def _all_requests(session: SimSession) -> pd.DataFrame:
    live = session.advances_df()
    if live.empty:
        return session.world.requests
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
    return pd.concat([session.world.requests, live_req], ignore_index=True)


def borrowing_monitor(session: SimSession) -> dict:
    """M5 over everyone who borrowed in the last 6 months. A flag means 'have a person look', never a decline."""
    feats = m5.behaviour_features(_all_requests(session), session.world.employees_all, session.sim_date)
    if feats.empty:
        return {"screened": 0, "unusual": 0, "chronic": 0, "top": []}
    flags = m5.abuse_check(feats, session.settings.policy)
    feats = feats.assign(
        anomaly_score=[f.anomaly_score for f in flags],
        unusual=[f.unusual_pattern for f in flags],
        chronic=[f.chronic for f in flags],
    )
    employers = session.world.employees_all.set_index("employee_id")["employer_id"]
    top = feats.sort_values(["anomaly_score", "employee_id"], ascending=[False, True]).head(MONITOR_TOP)
    return {
        "screened": int(len(feats)),
        "unusual": int(feats["unusual"].sum()),
        "chronic": int(feats["chronic"].sum()),
        "threshold_top_pct": session.settings.policy.abuse_flag_top_pct,
        "top": [
            {
                "employee_id": r.employee_id,
                "employer_id": employers.get(r.employee_id),
                "anomaly_score": round(float(r.anomaly_score), 3),
                "unusual": bool(r.unusual),
                "chronic": bool(r.chronic),
                "requests_6m": int(r.requests_6m),
                "streak_months": int(r.streak_months),
                "full_cap_share": round(float(r.full_cap_share), 2),
            }
            for r in top.itertuples()
        ],
    }
