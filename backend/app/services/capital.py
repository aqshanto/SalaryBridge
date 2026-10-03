"""Live capital view: the M4 portfolio forecast next to the money in the upay pool.

The live simulation only plays out the demo characters' advances, not the whole workforce, so the
forecast is the portfolio-level M4 forecast made from the seed history (origin = first live month,
three months ahead). A scenario can mark extra months as Eid months; the forecast then uses that flag.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.ml import m4_capital as m4
from app.sim.session import SimSession

TABLES = ("meta", "attendance", "employees", "advances", "advance_requests")


@lru_cache(maxsize=4)
def seed_panel(database_url: str) -> pd.DataFrame:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            tables = {t: pd.read_sql(f"select * from {t}", conn) for t in TABLES}
    finally:
        engine.dispose()
    return m4.monthly_panel(tables)


@lru_cache(maxsize=32)
def _forecast(database_url: str, extra_eid: frozenset[str], buffer_pct: float) -> dict:
    base = seed_panel(database_url)
    panel = base.copy()
    panel.attrs = {**base.attrs, "eid_months": set(base.attrs["eid_months"]) | set(extra_eid)}
    policy = type("P", (), {"capital_buffer_pct": buffer_pct})()
    return m4.capital_forecast(panel, origin=panel.attrs["months"], policy=policy)


def forecast(session: SimSession) -> dict:
    out = _forecast(session.settings.database_url, frozenset(session.extra_eid_months), session.settings.policy.capital_buffer_pct)
    month = session.sim_date.isoformat()[:7]
    months = out["months"]
    current = next((m for m in months if m["month"] == month), months[-1])
    pool = session.ledger.balance("upay_pool") // 100
    return {
        "origin_month": out["origin_month"],
        "model_version": out["model_version"],
        "buffer_pct": out["buffer_pct"],
        "months": [{**m, "is_eid": m["month"] in set(session.extra_eid_months) | seed_panel(session.settings.database_url).attrs["eid_months"]} for m in months],
        "current_month": current["month"],
        "current_required_pool_bdt": current["required_pool_bdt"],
        "beyond_forecast_window": current["month"] != month,
        "pool_bdt": int(pool),
        "headroom_bdt": int(pool - current["required_pool_bdt"]),
        "pool_below_required": pool < current["required_pool_bdt"],
        "per_employer": out["per_employer"],
    }
