"""Ops view of employers: live M1 payroll risk with reasons, exposure and status."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from app.ml import m1_employer as m1
from app.services.decision import _employer_runs
from app.sim import settlement
from app.sim.session import SimSession, sim_advances


def exposure_by_employer(session: SimSession) -> dict[str, int]:
    with session.engine.connect() as conn:
        rows = conn.execute(select(sim_advances).where(sim_advances.c.status.in_(("open", "carried_over", "pending_write_off")))).all()
        out: dict[str, int] = {}
        for adv in rows:
            out[adv.employer_id] = out.get(adv.employer_id, 0) + settlement.remaining(conn, adv)
    return out


def employer_table(session: SimSession) -> list[dict]:
    model = m1.load_model()
    today = session.sim_date
    closed = session.closed_employers()
    exposure = exposure_by_employer(session)
    employers = session.world.employers_all.set_index("employer_id", drop=False)
    ids = list(session.world.employers.index)
    rows, feats = [], []
    for eid in ids:
        runs = _employer_runs(session, eid)
        feats.append(m1.employer_features(employers.loc[eid], runs, today, session.settings.policy.grace_days))
        last = runs.sort_values("scheduled_date").iloc[-1] if len(runs) else None
        rows.append(
            {
                "employer_id": eid,
                "name": employers.at[eid, "name"],
                "industry": employers.at[eid, "industry"],
                "headcount": int(employers.at[eid, "headcount"]),
                "payroll_day": int(employers.at[eid, "payroll_day"]),
                "status": "closed" if eid in closed else "open",
                "last_payroll_status": last["status"] if last is not None else None,
                "exposure_bdt": int(exposure.get(eid, 0)),
            }
        )
    X = pd.DataFrame(feats)
    probs = model.predict_proba(X)
    reasons = model.reasons(X)
    for row, p, r in zip(rows, probs, reasons):
        if row["status"] == "closed":
            # A default is a known fact, not a prediction. M1 also never saw post-default months in
            # training (a defaulting employer closes), so its score here would be meaningless.
            row["prob_late"], row["risk_source"] = 1.0, "rule"
            row["reasons"] = [{"feature": "payroll_status", "code": "EMPLOYER_DEFAULTED", "direction": "raises_risk", "shap": None}]
        else:
            row["prob_late"], row["risk_source"] = round(float(p), 4), "m1"
            row["reasons"] = r
    rows.sort(key=lambda r: (-r["prob_late"], r["employer_id"]))
    return rows
