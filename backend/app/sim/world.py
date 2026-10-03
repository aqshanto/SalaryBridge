"""Read-only view of the seed database used to start every simulation session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from data.generator import PROFILES, ProfileParams, _add_months


@dataclass(frozen=True)
class SeedWorld:
    profile: ProfileParams
    seed: int
    first_live_month: date
    employers: pd.DataFrame  # open employers only, indexed by employer_id
    employees: pd.DataFrame  # employees still employed at the end of history, indexed by employee_id
    last_run_status: dict[str, str]
    # Full history, used to build model features for live decisions
    employers_all: pd.DataFrame
    employees_all: pd.DataFrame
    payroll_runs: pd.DataFrame
    advances: pd.DataFrame
    requests: pd.DataFrame


@lru_cache(maxsize=4)
def load_world(database_url: str) -> SeedWorld:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            meta = pd.read_sql("select * from meta", conn).iloc[0]
            employers = pd.read_sql("select * from employers", conn)
            employees_all = pd.read_sql("select * from employees", conn)
            runs = pd.read_sql("select * from payroll_runs order by employer_id, month_index", conn)
            advances = pd.read_sql("select * from advances", conn)
            requests = pd.read_sql("select * from advance_requests", conn)
    finally:
        engine.dispose()

    open_employers = employers[employers["closed_month_index"].isna()].set_index("employer_id", drop=False)
    employees = employees_all[employees_all["end_date"].isna()]
    employees = employees[employees["employer_id"].isin(open_employers.index)].set_index("employee_id", drop=False)
    start = date.fromisoformat(str(meta["start"])[:10])
    return SeedWorld(
        profile=PROFILES[str(meta["profile"])],
        seed=int(meta["seed"]),
        first_live_month=_add_months(start, int(meta["months"])),
        employers=open_employers,
        employees=employees,
        last_run_status=runs.groupby("employer_id")["status"].last().to_dict(),
        employers_all=employers,
        employees_all=employees_all,
        payroll_runs=runs,
        advances=advances,
        requests=requests,
    )
