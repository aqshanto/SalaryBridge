"""Ops view of employers: live M1 payroll risk with reasons, exposure and status."""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import timedelta

import pandas as pd
from sqlalchemy import Integer, func, select

from app.ml import m1_employer as m1
from app.services.decision import _employer_runs
from app.sim import settlement
from app.sim.session import SimSession, sim_advances, sim_payroll_runs


def exposure_by_employer(session: SimSession) -> dict[str, int]:
    with session.engine.connect() as conn:
        rows = conn.execute(select(sim_advances).where(sim_advances.c.status.in_(("open", "carried_over", "pending_write_off")))).all()
        out: dict[str, int] = {}
        for adv in rows:
            out[adv.employer_id] = out.get(adv.employer_id, 0) + settlement.remaining(conn, adv)
    return out


TREND_DAYS = 30

# M1 scores only change when the simulated date, the payroll runs or the closed employers change, so they are
# cached per session state (the ops page asked for all ~60 employers twice on every load: ~4 s on the free plan).
_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 64


def _state_key(session: SimSession, closed: dict) -> tuple:
    t = sim_payroll_runs.c
    with session.engine.connect() as conn:
        runs = conn.execute(select(func.count(), func.sum(t.paid.cast(Integer)), func.sum(t.delay_days), func.sum(t.paid_share), func.group_concat(t.status))).one()
    return (session.session_id, session.sim_date.isoformat(), tuple(sorted(closed)), *(runs[:4]), hash(runs[4]))


def employer_table(session: SimSession) -> list[dict]:
    """Live M1 payroll risk per employer, with the change since TREND_DAYS ago (same point-in-time features)."""
    model = m1.load_model()
    today = session.sim_date
    before = today - timedelta(days=TREND_DAYS)
    closed = session.closed_employers()
    exposure = exposure_by_employer(session)
    employers = session.world.employers_all.set_index("employer_id", drop=False)
    ids = list(session.world.employers.index)
    key = _state_key(session, closed)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is None:
        cached = _score(session, model, ids, employers, today, before)
        with _CACHE_LOCK:
            _CACHE[key] = cached
            while len(_CACHE) > _CACHE_SIZE:
                _CACHE.popitem(last=False)
    last_status, probs, probs_before, reasons = cached
    rows = []
    for eid in ids:
        rows.append(
            {
                "employer_id": eid,
                "name": employers.at[eid, "name"],
                "industry": employers.at[eid, "industry"],
                "headcount": int(employers.at[eid, "headcount"]),
                "payroll_day": int(employers.at[eid, "payroll_day"]),
                "status": "closed" if eid in closed else "open",
                "last_payroll_status": last_status[eid],
                "exposure_bdt": int(exposure.get(eid, 0)),
            }
        )
    for row, p, pb, r in zip(rows, probs, probs_before, reasons):
        row["prob_late_before"] = round(float(pb), 4)
        if row["status"] == "closed":
            # A default is a known fact, not a prediction. M1 also never saw post-default months in
            # training (a defaulting employer closes), so its score here would be meaningless.
            row["prob_late"], row["risk_source"] = 1.0, "rule"
            row["trend"] = round(1.0 - float(pb), 4)
            row["reasons"] = [{"feature": "payroll_status", "code": "EMPLOYER_DEFAULTED", "direction": "raises_risk", "shap": None}]
        else:
            row["prob_late"], row["risk_source"] = round(float(p), 4), "m1"
            row["trend"] = round(float(p) - float(pb), 4)
            row["reasons"] = r
    rows.sort(key=lambda r: (-r["prob_late"], r["employer_id"]))
    return rows


def _score(session: SimSession, model, ids, employers, today, before) -> tuple:
    feats, feats_before, last_status = [], [], {}
    grace = session.settings.policy.grace_days
    for eid in ids:
        runs = _employer_runs(session, eid)
        feats.append(m1.employer_features(employers.loc[eid], runs, today, grace))
        feats_before.append(m1.employer_features(employers.loc[eid], runs, before, grace))
        last_status[eid] = runs.sort_values("scheduled_date").iloc[-1]["status"] if len(runs) else None
    X = pd.DataFrame(feats)
    return last_status, model.predict_proba(X), model.predict_proba(pd.DataFrame(feats_before)), model.reasons(X)
