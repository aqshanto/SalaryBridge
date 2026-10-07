"""One simulation per browser session, each in its own SQLite file."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, Table, create_engine, delete, func, insert, select, update
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.ledger import Ledger, bdt_to_paisa, paisa_to_bdt
from app.sim import settlement
from app.sim.tables import (  # noqa: F401  (re-exported for services and tests)
    metadata,
    sim_advances,
    sim_attendance,
    sim_closed_employers,
    sim_decisions,
    sim_employer_settings,
    sim_meta,
    sim_notice_acks,
    sim_overrides,
    sim_payroll_runs,
    sim_repayments,
    sim_resignations,
    sim_reviews,
)
from app.sim.world import SeedWorld
from data.generator import _add_months, draw_payroll_outcome

SESSION_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
MAX_ADVANCE_DAYS = 120


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


# The first page load sends several requests with a brand-new session id at once; without a lock two of them
# create and initialise the same SQLite file and one fails ("table sim_meta already exists").
_OPEN_LOCKS: dict[str, threading.Lock] = {}
_OPEN_LOCKS_GUARD = threading.Lock()


def _open_lock(session_id: str) -> threading.Lock:
    with _OPEN_LOCKS_GUARD:
        return _OPEN_LOCKS.setdefault(session_id, threading.Lock())


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
        with _open_lock(self.session_id):
            return self._open()

    def _open(self) -> "SimSession":
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
        with _open_lock(self.session_id):
            self.path.unlink(missing_ok=True)
            return self._open()

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

    # ---------- employers, people, scenarios ----------
    def closed_employers(self) -> dict[str, date]:
        with self.engine.connect() as conn:
            return {r.employer_id: r.closed_on for r in conn.execute(select(sim_closed_employers))}

    def open_employer_ids(self) -> list[str]:
        closed = self.closed_employers()
        return [e for e in self.world.employers.index if e not in closed]

    def resignation(self, employee_id: str) -> date | None:
        with self.engine.connect() as conn:
            return settlement.end_date(conn, employee_id)

    def resign(self, employee_id: str, end: date, reason: str = "voluntary") -> None:
        if employee_id not in self.world.employees.index:
            raise SessionError(f"Unknown or inactive employee: {employee_id}")
        with self.engine.begin() as conn:
            conn.execute(delete(sim_resignations).where(sim_resignations.c.employee_id == employee_id))
            conn.execute(insert(sim_resignations).values(employee_id=employee_id, end_date=end, reason=reason))

    def force_payroll(self, employer_id: str, work_month: str, status: str, delay_days: int = 0, paid_share: float = 1.0) -> None:
        """Make an upcoming payroll run come out a certain way (scenario buttons)."""
        if employer_id not in self.world.employers.index:
            raise SessionError(f"Unknown employer: {employer_id}")
        with self.engine.begin() as conn:
            conn.execute(delete(sim_overrides).where(sim_overrides.c.employer_id == employer_id, sim_overrides.c.work_month == work_month))
            conn.execute(insert(sim_overrides).values(employer_id=employer_id, work_month=work_month, status=status, delay_days=delay_days, paid_share=paid_share))

    def employer_settings(self, employer_id: str) -> dict:
        """HR settings for an employer: opted in, and its own cap (never above the policy cap)."""
        with self.engine.connect() as conn:
            row = conn.execute(select(sim_employer_settings).where(sim_employer_settings.c.employer_id == employer_id)).first()
        if row is not None:
            return {"opted_in": bool(row.opted_in), "cap_pct": float(row.cap_pct)}
        opted = bool(self.world.employers_all.set_index("employer_id").at[employer_id, "opted_in"])
        return {"opted_in": opted, "cap_pct": float(self.settings.policy.cap_pct_of_salary)}

    def set_employer_settings(self, employer_id: str, opted_in: bool, cap_pct: float) -> dict:
        policy_cap = self.settings.policy.cap_pct_of_salary
        if not 0 < cap_pct <= policy_cap:
            raise SessionError(f"cap_pct must be above 0 and at most the upay limit of {policy_cap:g}%")
        with self.engine.begin() as conn:
            conn.execute(delete(sim_employer_settings).where(sim_employer_settings.c.employer_id == employer_id))
            conn.execute(insert(sim_employer_settings).values(employer_id=employer_id, opted_in=opted_in, cap_pct=cap_pct))
        return self.employer_settings(employer_id)

    def record_attendance(self, employee_id: str, work_month: str, unpaid_absent_days: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sim_attendance).where(sim_attendance.c.employee_id == employee_id, sim_attendance.c.work_month == work_month))
            conn.execute(insert(sim_attendance).values(employee_id=employee_id, work_month=work_month, unpaid_absent_days=unpaid_absent_days, received_on=self.sim_date))

    def unpaid_absence_days(self, employee_id: str, work_month: str) -> int | None:
        with self.engine.connect() as conn:
            return conn.execute(
                select(sim_attendance.c.unpaid_absent_days).where(sim_attendance.c.employee_id == employee_id, sim_attendance.c.work_month == work_month)
            ).scalar_one_or_none()

    def ack_notice(self, employer_id: str, payday: date, total_bdt: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sim_notice_acks).where(sim_notice_acks.c.employer_id == employer_id, sim_notice_acks.c.payday == payday))
            conn.execute(insert(sim_notice_acks).values(employer_id=employer_id, payday=payday, total_bdt=total_bdt, acked_on=self.sim_date))

    def notice_ack(self, employer_id: str, payday: date) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(sim_notice_acks).where(sim_notice_acks.c.employer_id == employer_id, sim_notice_acks.c.payday == payday)
            ).first()
        return {"total_bdt": row.total_bdt, "acked_on": row.acked_on.isoformat()} if row else None

    @property
    def extra_eid_months(self) -> list[str]:
        with self.engine.connect() as conn:
            value = conn.execute(select(sim_meta.c.value).where(sim_meta.c.key == "extra_eid_months")).scalar_one_or_none()
        return sorted(m for m in (value or "").split(",") if m)

    def add_eid_months(self, months: list[str]) -> None:
        value = ",".join(sorted(set(self.extra_eid_months) | set(months)))
        with self.engine.begin() as conn:
            conn.execute(delete(sim_meta).where(sim_meta.c.key == "extra_eid_months"))
            conn.execute(insert(sim_meta).values(key="extra_eid_months", value=value))

    def set_pool(self, target_bdt: int, memo: str) -> None:
        """Move money between upay_capital and the advance pool so the pool holds `target_bdt`."""
        diff = bdt_to_paisa(int(target_bdt)) - self.ledger.balance("upay_pool")
        if diff:
            self.ledger.post_entry(self.sim_date, "pool_adjustment", [("upay_pool", diff), ("upay_capital", -diff)], memo=memo)

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

    # ---------- payroll and settlement ----------
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
        settlement.spend_new_advances(self, day)
        work_month = _add_months(day.replace(day=1), -1)
        defaulted = []
        with self.engine.begin() as conn:
            if work_month >= self.world.first_live_month:
                for eid in self.open_employer_ids():
                    emp = self.world.employers.loc[eid]
                    if int(emp["payroll_day"]) != day.day:
                        continue
                    event = self._schedule_run(conn, eid, emp, work_month, day)
                    events.append(event)
                    if event["status"] == "default":
                        defaulted.append(eid)
            due = conn.execute(
                select(sim_payroll_runs).where(sim_payroll_runs.c.actual_date == day, sim_payroll_runs.c.paid.is_(False))
            ).all()
        for eid in defaulted:
            # No wages and no remittance: try wallets, then everything waits for write-off.
            events += settlement.wallet_and_carry(self, eid, day, day, employer_open=False)
        for run in due:
            events += self._pay_run(run, day)
        events += settlement.daily_checks(self, day)
        return events

    def _schedule_run(self, conn, employer_id: str, emp, work_month: date, day: date) -> dict:
        month_key = work_month.isoformat()[:7]
        forced = conn.execute(
            select(sim_overrides).where(sim_overrides.c.employer_id == employer_id, sim_overrides.c.work_month == month_key)
        ).first()
        if forced is not None:
            status, delay, paid_share = forced.status, forced.delay_days, forced.paid_share
        else:
            month_number = work_month.year * 12 + work_month.month
            rng = np.random.default_rng([self.world.seed, int(employer_id[1:]), month_number])
            prev_late = self._previous_status(conn, employer_id) in ("late", "partial")
            status, delay, paid_share = draw_payroll_outcome(rng, self.world.profile, emp["reliability_type"], prev_late)
        actual = None if status == "default" else day + timedelta(days=delay)
        conn.execute(
            insert(sim_payroll_runs).values(
                employer_id=employer_id,
                work_month=month_key,
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
            "work_month": month_key,
            "status": status,
            "actual_date": actual.isoformat() if actual else None,
            "forced": forced is not None,
        }

    def _pay_run(self, run, day: date) -> list[dict]:
        """Employer pays: one bulk deduction remittance, then net wages into wallets of staff paid via upay."""
        withheld, events = settlement.employer_remittance(self, run, day)

        with self.engine.connect() as conn:
            gone = {
                r.employee_id
                for r in conn.execute(select(sim_resignations).where(sim_resignations.c.end_date < run.scheduled_date))
            }
        staff = self.world.employees[
            (self.world.employees["employer_id"] == run.employer_id) & self.world.employees["salary_to_upay"].astype(bool)
        ]
        share_pct = round(run.paid_share * 100)
        settlement.spend_down_wallets(self, {e: s for e, s in staff["salary_bdt"].items() if e not in gone}, day)
        credits = []
        for eid, salary in staff["salary_bdt"].items():
            if eid in gone:
                continue
            gross = bdt_to_paisa(int(salary)) * share_pct // 100
            net = gross - bdt_to_paisa(withheld.get(eid, 0))
            if net > 0:
                credits.append((f"employee_wallet:{eid}", net))
        total = sum(amount for _, amount in credits)
        if credits:
            self.ledger.post_entry(day, "salary_credit", credits + [(f"employer:{run.employer_id}", -total)], ref=f"{run.employer_id}:{run.work_month}")
        with self.engine.begin() as conn:
            conn.execute(
                update(sim_payroll_runs)
                .where(sim_payroll_runs.c.employer_id == run.employer_id, sim_payroll_runs.c.work_month == run.work_month)
                .values(paid=True)
            )
        events.insert(
            0,
            {
                "date": day.isoformat(),
                "type": "payroll_paid",
                "employer_id": run.employer_id,
                "work_month": run.work_month,
                "status": run.status,
                "wallet_credits": len(credits),
                "wallet_total_bdt": paisa_to_bdt(total),
                "remitted_bdt": sum(withheld.values()),
            },
        )
        events += settlement.wallet_and_carry(self, run.employer_id, run.scheduled_date, day, employer_open=run.employer_id not in self.closed_employers())
        return events

    # ---------- reads used by the services ----------
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

    def repayments_df(self, advance_id: str | None = None) -> pd.DataFrame:
        query = select(sim_repayments).order_by(sim_repayments.c.repayment_id)
        if advance_id:
            query = query.where(sim_repayments.c.advance_id == advance_id)
        with self.engine.connect() as conn:
            return pd.DataFrame(conn.execute(query).mappings().all(), columns=[c.name for c in sim_repayments.columns])

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

    def ledger_balances(self) -> dict:
        """Balances in BDT grouped for the money-flow panel. All groups always sum to zero."""
        balances = self.ledger.balances()
        group = lambda prefix: sum(v for k, v in balances.items() if k.startswith(prefix))  # noqa: E731
        bdt = lambda paisa: round(paisa / 100, 2)  # noqa: E731
        return {
            "upay_capital": bdt(balances.get("upay_capital", 0)),
            "upay_pool": bdt(balances.get("upay_pool", 0)),
            "employee_wallets": bdt(group("employee_wallet:")),
            "employers": bdt(group("employer:")),
            "fees_income": bdt(balances.get("fees_income", 0)),
            "loss_provision": bdt(balances.get("loss_provision", 0)),
            "external_spend": bdt(balances.get("external_spend", 0)),
        }

    # ---------- state ----------
    def state(self) -> dict:
        today = self.sim_date
        recon = self.ledger.reconcile()
        with self.engine.connect() as conn:
            runs = conn.execute(select(sim_payroll_runs).order_by(sim_payroll_runs.c.scheduled_date.desc()).limit(10)).all()
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
            "kill_switch": self.kill_switch,
            "pool_bdt": paisa_to_bdt(self.ledger.balance("upay_pool")),
            "advances": settlement.summary(self),
            "ledger": {"reconciled": recon.ok, "entries": recon.entries, "total_paisa": recon.total_paisa, "balances_bdt": self.ledger_balances()},
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
