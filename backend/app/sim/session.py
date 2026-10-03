"""One simulation per browser session, each in its own SQLite file."""

from __future__ import annotations

import os
import re
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Boolean, Column, Date, Engine, Float, Integer, MetaData, String, Table, create_engine, delete, func, insert, select, update
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.ledger import Ledger, bdt_to_paisa, paisa_to_bdt
from app.sim.world import SeedWorld
from data.generator import _add_months, draw_payroll_outcome

SESSION_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
MAX_ADVANCE_DAYS = 120

metadata = MetaData()

sim_meta = Table(
    "sim_meta",
    metadata,
    Column("key", String(40), primary_key=True),
    Column("value", String(200), nullable=False),
)

sim_payroll_runs = Table(
    "sim_payroll_runs",
    metadata,
    Column("employer_id", String(10), primary_key=True),
    Column("work_month", String(7), primary_key=True),
    Column("scheduled_date", Date, nullable=False),
    Column("actual_date", Date),
    Column("status", String(10), nullable=False),
    Column("delay_days", Integer, nullable=False),
    Column("paid_share", Float, nullable=False),
    Column("paid", Boolean, nullable=False, default=False),
)

sim_closed_employers = Table(
    "sim_closed_employers",
    metadata,
    Column("employer_id", String(10), primary_key=True),
    Column("closed_on", Date, nullable=False),
)

# Advances paid out in this session (written when an offer is accepted, F13).
sim_advances = Table(
    "sim_advances",
    metadata,
    Column("advance_id", String(20), primary_key=True),
    Column("decision_id", String(20), nullable=False),
    Column("employee_id", String(30), nullable=False, index=True),
    Column("employer_id", String(10), nullable=False),
    Column("work_month", String(7), nullable=False),
    Column("issue_date", Date, nullable=False),
    Column("amount_requested", Integer, nullable=False),
    Column("hard_cap", Integer, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("fee", Integer, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("grace_end", Date, nullable=False),
    Column("tier", String(1), nullable=False),
    Column("status", String(12), nullable=False),  # open | recovered | carried_over | written_off
    Column("recovered_by_grace", Boolean),
    Column("settled_date", Date),
)

# Every offer decision, with its inputs and model versions (audit log).
sim_decisions = Table(
    "sim_decisions",
    metadata,
    Column("decision_id", String(20), primary_key=True),
    Column("sim_date", Date, nullable=False),
    Column("employee_id", String(30), nullable=False, index=True),
    Column("requested_bdt", Integer, nullable=False),
    Column("status", String(10), nullable=False),
    Column("approved_bdt", Integer, nullable=False),
    Column("payload", String, nullable=False),  # JSON: inputs, features, outputs, model versions
)

# What happened to a decision afterwards: accepted by the employee, or approved/rejected by ops.
sim_reviews = Table(
    "sim_reviews",
    metadata,
    Column("decision_id", String(20), primary_key=True),
    Column("action", String(10), nullable=False),  # accepted | approved | rejected
    Column("actor", String(20), nullable=False),  # employee | ops
    Column("note", String(500)),
    Column("sim_date", Date, nullable=False),
    Column("amount_bdt", Integer, nullable=False),
    Column("advance_id", String(20)),
)


class SessionError(ValueError):
    pass


def sessions_dir(settings: Settings) -> Path:
    path = Path(settings.sim_dir) if settings.sim_dir else Path(tempfile.gettempdir()) / "salarybridge_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_expired(settings: Settings) -> int:
    """Delete session files not used within the TTL. Returns how many were removed."""
    cutoff = time.time() - settings.session_ttl_hours * 3600
    removed = 0
    for path in sessions_dir(settings).glob("*.db"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


class SimSession:
    def __init__(self, session_id: str, settings: Settings, world: SeedWorld):
        if not SESSION_ID.match(session_id):
            raise SessionError("X-Session-Id must be 8–64 letters, digits or dashes")
        self.session_id = session_id
        self.settings = settings
        self.world = world
        self.path = sessions_dir(settings) / f"{session_id}.db"

    # ---------- lifecycle ----------
    def open(self) -> "SimSession":
        is_new = not self.path.exists()
        if is_new:
            cleanup_expired(self.settings)
        self.engine: Engine = create_engine(f"sqlite:///{self.path.as_posix()}", poolclass=NullPool)
        metadata.create_all(self.engine)
        self.ledger = Ledger(self.engine)
        if is_new:
            self._initialise()
        os.utime(self.path)
        return self

    def reset(self) -> "SimSession":
        self.path.unlink(missing_ok=True)
        return self.open()

    def _initialise(self) -> None:
        start = self.world.first_live_month.replace(day=self.settings.sim_start_day)
        with self.engine.begin() as conn:
            conn.execute(
                insert(sim_meta),
                [
                    {"key": "sim_date", "value": start.isoformat()},
                    {"key": "start_date", "value": start.isoformat()},
                    {"key": "seed", "value": str(self.world.seed)},
                ],
            )
        pool = bdt_to_paisa(self.settings.policy.initial_pool_bdt)
        self.ledger.post_entry(start, "capital", [("upay_pool", pool), ("upay_capital", -pool)], memo="Initial advance pool")

    # ---------- clock ----------
    def _meta(self, key: str) -> str:
        with self.engine.connect() as conn:
            return conn.execute(select(sim_meta.c.value).where(sim_meta.c.key == key)).scalar_one()

    @property
    def sim_date(self) -> date:
        return date.fromisoformat(self._meta("sim_date"))

    def _set_sim_date(self, d: date) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(sim_meta).where(sim_meta.c.key == "sim_date").values(value=d.isoformat()))

    def advance(self, days: int) -> list[dict]:
        if not 1 <= days <= MAX_ADVANCE_DAYS:
            raise SessionError(f"days must be between 1 and {MAX_ADVANCE_DAYS}")
        return self.advance_to(self.sim_date + timedelta(days=days))

    def advance_to(self, target: date) -> list[dict]:
        events: list[dict] = []
        day = self.sim_date
        while day < target:
            day += timedelta(days=1)
            events += self._run_day(day)
            self._set_sim_date(day)
        return events

    # ---------- payroll ----------
    def closed_employers(self) -> dict[str, date]:
        with self.engine.connect() as conn:
            return {r.employer_id: r.closed_on for r in conn.execute(select(sim_closed_employers))}

    def open_employer_ids(self) -> list[str]:
        closed = self.closed_employers()
        return [e for e in self.world.employers.index if e not in closed]

    def next_payday(self, employer_id: str | None = None) -> date:
        ids = [employer_id] if employer_id else self.open_employer_ids()
        if not ids:
            raise SessionError("No open employers")
        if employer_id and employer_id not in self.open_employer_ids():
            raise SessionError(f"Employer {employer_id} is not open in this session")
        today = self.sim_date
        candidates = []
        for eid in ids:
            payroll_day = int(self.world.employers.at[eid, "payroll_day"])
            d = today.replace(day=payroll_day) if today.day < payroll_day else _add_months(today, 1).replace(day=payroll_day)
            candidates.append(d)
        return min(candidates)

    def _previous_status(self, conn, employer_id: str) -> str:
        last = conn.execute(
            select(sim_payroll_runs.c.status)
            .where(sim_payroll_runs.c.employer_id == employer_id)
            .order_by(sim_payroll_runs.c.work_month.desc())
            .limit(1)
        ).scalar_one_or_none()
        return last or self.world.last_run_status.get(employer_id, "on_time")

    def _run_day(self, day: date) -> list[dict]:
        events = []
        work_month = _add_months(day.replace(day=1), -1)
        with self.engine.begin() as conn:
            if work_month >= self.world.first_live_month:
                for eid in self.open_employer_ids():
                    emp = self.world.employers.loc[eid]
                    if int(emp["payroll_day"]) != day.day:
                        continue
                    events.append(self._schedule_run(conn, eid, emp, work_month, day))
            due = conn.execute(
                select(sim_payroll_runs).where(sim_payroll_runs.c.actual_date == day, sim_payroll_runs.c.paid.is_(False))
            ).all()
        for run in due:
            events.append(self._pay_run(run, day))
        return events

    def _schedule_run(self, conn, employer_id: str, emp, work_month: date, day: date) -> dict:
        month_number = work_month.year * 12 + work_month.month
        employer_number = int(employer_id[1:])
        rng = np.random.default_rng([self.world.seed, employer_number, month_number])
        prev_late = self._previous_status(conn, employer_id) in ("late", "partial")
        status, delay, paid_share = draw_payroll_outcome(rng, self.world.profile, emp["reliability_type"], prev_late)
        actual = None if status == "default" else day + timedelta(days=delay)
        conn.execute(
            insert(sim_payroll_runs).values(
                employer_id=employer_id,
                work_month=work_month.isoformat()[:7],
                scheduled_date=day,
                actual_date=actual,
                status=status,
                delay_days=delay,
                paid_share=paid_share,
                paid=False,
            )
        )
        if status == "default":
            conn.execute(insert(sim_closed_employers).values(employer_id=employer_id, closed_on=day))
        return {
            "date": day.isoformat(),
            "type": "payroll_default" if status == "default" else "payroll_scheduled",
            "employer_id": employer_id,
            "work_month": work_month.isoformat()[:7],
            "status": status,
            "actual_date": actual.isoformat() if actual else None,
        }

    def _pay_run(self, run, day: date) -> dict:
        """Wages are paid outside upay except for staff who receive salary in their upay wallet."""
        staff = self.world.employees[
            (self.world.employees["employer_id"] == run.employer_id) & self.world.employees["salary_to_upay"].astype(bool)
        ]
        share_pct = round(run.paid_share * 100)
        credits = [
            (f"employee_wallet:{eid}", bdt_to_paisa(int(salary)) * share_pct // 100)
            for eid, salary in staff["salary_bdt"].items()
        ]
        credits = [(account, amount) for account, amount in credits if amount > 0]
        total = sum(amount for _, amount in credits)
        if credits:
            self.ledger.post_entry(
                day,
                "salary_credit",
                credits + [(f"employer:{run.employer_id}", -total)],
                ref=f"{run.employer_id}:{run.work_month}",
            )
        with self.engine.begin() as conn:
            conn.execute(
                update(sim_payroll_runs)
                .where(sim_payroll_runs.c.employer_id == run.employer_id, sim_payroll_runs.c.work_month == run.work_month)
                .values(paid=True)
            )
        return {
            "date": day.isoformat(),
            "type": "payroll_paid",
            "employer_id": run.employer_id,
            "work_month": run.work_month,
            "status": run.status,
            "wallet_credits": len(credits),
            "wallet_total_bdt": paisa_to_bdt(total),
        }

    # ---------- reads used by the decision service ----------
    @property
    def kill_switch(self) -> bool:
        with self.engine.connect() as conn:
            value = conn.execute(select(sim_meta.c.value).where(sim_meta.c.key == "kill_switch")).scalar_one_or_none()
        return value == "on"

    def payroll_runs_df(self, employer_id: str | None = None) -> pd.DataFrame:
        query = select(sim_payroll_runs)
        if employer_id:
            query = query.where(sim_payroll_runs.c.employer_id == employer_id)
        with self.engine.connect() as conn:
            return pd.DataFrame(conn.execute(query).mappings().all(), columns=[c.name for c in sim_payroll_runs.columns])

    def advances_df(self, employee_id: str | None = None) -> pd.DataFrame:
        query = select(sim_advances)
        if employee_id:
            query = query.where(sim_advances.c.employee_id == employee_id)
        with self.engine.connect() as conn:
            return pd.DataFrame(conn.execute(query).mappings().all(), columns=[c.name for c in sim_advances.columns])

    def next_id(self, table: Table, prefix: str) -> str:
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(table)).scalar_one()
        return f"{prefix}{count + 1:06d}"

    def set_kill_switch(self, on: bool) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sim_meta).where(sim_meta.c.key == "kill_switch"))
            conn.execute(insert(sim_meta).values(key="kill_switch", value="on" if on else "off"))

    def decision(self, decision_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(sim_decisions).where(sim_decisions.c.decision_id == decision_id)).mappings().one_or_none()
            review = conn.execute(select(sim_reviews).where(sim_reviews.c.decision_id == decision_id)).mappings().one_or_none()
        if row is None:
            return None
        return {**dict(row), "review": dict(review) if review else None}

    def log_decision(self, row: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(sim_decisions).values(**row))

    # ---------- state ----------
    def state(self) -> dict:
        today = self.sim_date
        recon = self.ledger.reconcile()
        with self.engine.connect() as conn:
            runs = conn.execute(
                select(sim_payroll_runs).order_by(sim_payroll_runs.c.scheduled_date.desc()).limit(10)
            ).all()
        next_day = self.next_payday() if self.open_employer_ids() else None
        return {
            "session_id": self.session_id,
            "sim_date": today.isoformat(),
            "start_date": self._meta("start_date"),
            "work_month": today.isoformat()[:7],
            "day_of_month": today.day,
            "next_payday": next_day.isoformat() if next_day else None,
            "days_to_next_payday": (next_day - today).days if next_day else None,
            "open_employers": len(self.open_employer_ids()),
            "closed_employers": sorted(self.closed_employers()),
            "pool_bdt": paisa_to_bdt(self.ledger.balance("upay_pool")),
            "ledger": {"reconciled": recon.ok, "entries": recon.entries, "total_paisa": recon.total_paisa},
            "recent_payroll_runs": [
                {
                    "employer_id": r.employer_id,
                    "work_month": r.work_month,
                    "scheduled_date": r.scheduled_date.isoformat(),
                    "actual_date": r.actual_date.isoformat() if r.actual_date else None,
                    "status": r.status,
                    "paid": r.paid,
                }
                for r in runs
            ],
        }
