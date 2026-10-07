"""Payday settlement and the recovery waterfall for advances in a simulation session.

Waterfall for each advance due on an employer's payday:
1. employer remittance: the employer sends one bulk transfer for every deduction on its notice
   (paid_share of it if payroll is partial; for someone who left before payday, final-settlement
   pay is withheld with probability `final_settlement_recovery_prob`; nothing on default);
2. wallet auto-debit: whatever is still owed is taken from the employee's upay wallet, up to its
   balance (consent is assumed for everyone in the simulation [ASSUMPTION]);
3. carry-over: if still owed and the person is employed at an employer that is still paying, the
   rest is collected on the next payday;
4. write-off: anything still owed `writeoff_after_days` after the due date moves to loss_provision.

Collections pay principal first, then the fee. Ledger: payer -> upay_pool (principal) + fees_income (fee).
A write-off moves the unpaid principal from upay_capital to loss_provision (the cash already left the pool).

Spending [ASSUMPTION]: an advance is spent (cash-out or payments) the day after it is paid, and wages
paid into a upay wallet are spent during the month, leaving `wallet_savings_share` of the last wage
credit on the next payday. Without this, every advance would still be sitting in the wallet and the
wallet step would always recover it, hiding real losses.
"""

from __future__ import annotations

import zlib
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func, insert, select, update

from app.ledger import bdt_to_paisa
from app.sim.tables import sim_advances, sim_repayments, sim_resignations

if TYPE_CHECKING:
    from app.sim.session import SimSession

COLLECTING = ("open", "carried_over")


def collected(conn, advance_id: str) -> int:
    return int(
        conn.execute(
            select(func.coalesce(func.sum(sim_repayments.c.amount), 0)).where(
                sim_repayments.c.advance_id == advance_id, sim_repayments.c.step != "write_off"
            )
        ).scalar_one()
    )


def remaining(conn, adv) -> int:
    return adv.amount + adv.fee - collected(conn, adv.advance_id)


def end_date(conn, employee_id: str) -> date | None:
    return conn.execute(select(sim_resignations.c.end_date).where(sim_resignations.c.employee_id == employee_id)).scalar_one_or_none()


def _collect(session: "SimSession", adv, day: date, amount: int, step: str, source: str) -> int:
    """Post one collection (principal first, then fee). Returns the amount actually collected."""
    with session.engine.connect() as conn:
        already = collected(conn, adv.advance_id)
    amount = min(amount, adv.amount + adv.fee - already)
    if amount <= 0:
        return 0
    principal = max(0, min(amount, adv.amount - already))
    fee = amount - principal
    lines = [(source, -bdt_to_paisa(amount))]
    if principal:
        lines.append(("upay_pool", bdt_to_paisa(principal)))
    if fee:
        lines.append(("fees_income", bdt_to_paisa(fee)))
    session.ledger.post_entry(day, step, lines, ref=adv.advance_id)
    with session.engine.begin() as conn:
        conn.execute(insert(sim_repayments).values(advance_id=adv.advance_id, date=day, amount=amount, step=step))
        if remaining(conn, adv) == 0:
            conn.execute(update(sim_advances).where(sim_advances.c.advance_id == adv.advance_id).values(status="recovered", settled_date=day))
    return amount


def _final_settlement_succeeds(session: "SimSession", advance_id: str) -> bool:
    rng = np.random.default_rng([session.world.seed, zlib.crc32(advance_id.encode())])
    return bool(rng.random() < session.world.profile.final_settlement_recovery_prob)


def due_on_run(session: "SimSession", employer_id: str, scheduled: date) -> list:
    """Advances this payroll run must settle: due on its scheduled date, plus earlier carry-overs."""
    with session.engine.connect() as conn:
        return conn.execute(
            select(sim_advances)
            .where(
                sim_advances.c.employer_id == employer_id,
                (
                    ((sim_advances.c.status == "open") & (sim_advances.c.due_date == scheduled))
                    | ((sim_advances.c.status == "carried_over") & (sim_advances.c.due_date < scheduled))
                ),
            )
            .order_by(sim_advances.c.advance_id)
        ).all()


def employer_remittance(session: "SimSession", run, day: date) -> tuple[dict[str, int], list[dict]]:
    """Step 1 on the day wages are paid. Returns {employee_id: BDT withheld from wages} and events."""
    withheld: dict[str, int] = {}
    events = []
    source = f"employer:{run.employer_id}"
    for adv in due_on_run(session, run.employer_id, run.scheduled_date):
        with session.engine.connect() as conn:
            owed = remaining(conn, adv)
            left = end_date(conn, adv.employee_id)
        if left is not None and left < run.scheduled_date:
            amount = owed if _final_settlement_succeeds(session, adv.advance_id) else 0
            step = "employer_remittance"
        elif adv.status == "carried_over":
            amount, step = int(round(owed * run.paid_share)), "carry_over"
        else:
            amount, step = int(round(owed * run.paid_share)), "employer_remittance"
        got = _collect(session, adv, day, amount, step, source) if amount else 0
        withheld[adv.employee_id] = withheld.get(adv.employee_id, 0) + got
        events.append({"date": day.isoformat(), "type": "deduction", "advance_id": adv.advance_id, "employee_id": adv.employee_id, "step": step, "amount_bdt": got, "owed_before_bdt": owed})
    return withheld, events


def wallet_and_carry(session: "SimSession", employer_id: str, scheduled: date, day: date, employer_open: bool) -> list[dict]:
    """Steps 2 and 3 after the employer step (or after a default)."""
    events = []
    for adv in due_on_run(session, employer_id, scheduled):
        with session.engine.connect() as conn:
            owed = remaining(conn, adv)
            left = end_date(conn, adv.employee_id)
        if owed <= 0:
            continue
        wallet = f"employee_wallet:{adv.employee_id}"
        balance_bdt = session.ledger.balance(wallet) // 100
        if balance_bdt > 0:
            got = _collect(session, adv, day, min(owed, balance_bdt), "wallet_debit", wallet)
            owed -= got
            if got:
                events.append({"date": day.isoformat(), "type": "wallet_debit", "advance_id": adv.advance_id, "employee_id": adv.employee_id, "amount_bdt": got})
        if owed <= 0:
            continue
        employed = left is None or left > scheduled
        if adv.status == "open" and employed and employer_open:
            new_status = "carried_over"
        else:
            new_status = "pending_write_off"
        with session.engine.begin() as conn:
            conn.execute(update(sim_advances).where(sim_advances.c.advance_id == adv.advance_id).values(status=new_status))
        events.append({"date": day.isoformat(), "type": new_status, "advance_id": adv.advance_id, "employee_id": adv.employee_id, "owed_bdt": owed})
    return events


def daily_checks(session: "SimSession", day: date) -> list[dict]:
    """Grace-period flag and write-offs. Runs at the end of every simulated day."""
    events = []
    policy = session.settings.policy
    with session.engine.connect() as conn:
        graced = conn.execute(select(sim_advances).where(sim_advances.c.grace_end < day, sim_advances.c.recovered_by_grace.is_(None))).all()
    for adv in graced:
        with session.engine.begin() as conn:
            on_time = remaining(conn, adv) == 0 and adv.settled_date is not None and adv.settled_date <= adv.grace_end
            conn.execute(update(sim_advances).where(sim_advances.c.advance_id == adv.advance_id).values(recovered_by_grace=on_time))
        if not on_time:
            events.append({"date": day.isoformat(), "type": "missed_grace", "advance_id": adv.advance_id, "employee_id": adv.employee_id})

    cutoff = day - timedelta(days=policy.writeoff_after_days)
    with session.engine.connect() as conn:
        stale = conn.execute(
            select(sim_advances).where(sim_advances.c.status.in_(("open", "carried_over", "pending_write_off")), sim_advances.c.due_date <= cutoff)
        ).all()
    for adv in stale:
        with session.engine.connect() as conn:
            owed = remaining(conn, adv)
            principal_left = max(0, adv.amount - collected(conn, adv.advance_id))
        if owed <= 0:
            continue
        if principal_left:
            session.ledger.post_entry(
                day, "write_off", [("loss_provision", bdt_to_paisa(principal_left)), ("upay_capital", -bdt_to_paisa(principal_left))], ref=adv.advance_id
            )
        with session.engine.begin() as conn:
            conn.execute(insert(sim_repayments).values(advance_id=adv.advance_id, date=day, amount=owed, step="write_off"))
            conn.execute(update(sim_advances).where(sim_advances.c.advance_id == adv.advance_id).values(status="written_off", settled_date=day))
        events.append({"date": day.isoformat(), "type": "write_off", "advance_id": adv.advance_id, "employee_id": adv.employee_id, "amount_bdt": owed, "principal_bdt": principal_left})
    return events


def spend_new_advances(session: "SimSession", day: date) -> None:
    """Advances paid yesterday are spent today."""
    with session.engine.connect() as conn:
        rows = conn.execute(select(sim_advances).where(sim_advances.c.issue_date == day - timedelta(days=1))).all()
    lines = []
    for adv in rows:
        wallet = f"employee_wallet:{adv.employee_id}"
        spend = min(bdt_to_paisa(adv.amount), session.ledger.balance(wallet))
        if spend > 0:
            lines.append((wallet, -spend))
    if lines:
        session.ledger.post_entry(day, "spend", lines + [("external_spend", -sum(a for _, a in lines))], memo="Advances spent")


def spend_down_wallets(session: "SimSession", salaries: dict[str, int], day: date) -> None:
    """Before new wages land, last month's wages are spent down to the savings share."""
    keep_share = session.world.profile.wallet_savings_share
    balances = session.ledger.balances("employee_wallet:")
    lines = []
    for employee_id, salary in salaries.items():
        wallet = f"employee_wallet:{employee_id}"
        keep = int(bdt_to_paisa(int(salary)) * keep_share)
        excess = balances.get(wallet, 0) - keep
        if excess > 0:
            lines.append((wallet, -excess))
    if lines:
        session.ledger.post_entry(day, "spend", lines + [("external_spend", -sum(a for _, a in lines))], memo="Wages spent during the month")


def deduction_notice(session: "SimSession", employer_id: str) -> dict:
    """The one list HR needs: who has a deduction on the next payday and the single total to remit."""
    today = session.sim_date
    with session.engine.connect() as conn:
        rows = conn.execute(
            select(sim_advances).where(sim_advances.c.employer_id == employer_id, sim_advances.c.status.in_(COLLECTING)).order_by(sim_advances.c.employee_id, sim_advances.c.advance_id)
        ).all()
        items = [
            {
                "advance_id": r.advance_id,
                "employee_id": r.employee_id,
                "kind": "carried_over" if r.status == "carried_over" else "new",
                "issue_date": r.issue_date.isoformat(),
                "amount_due_bdt": remaining(conn, r),
            }
            for r in rows
        ]
    open_due = [r.due_date for r in rows if r.status == "open"]
    payday = min(open_due) if open_due else (session.next_payday(employer_id) if employer_id in session.open_employer_ids() else None)
    return {
        "employer_id": employer_id,
        "generated_on": today.isoformat(),
        "payday": payday.isoformat() if payday else None,
        "items": items,
        "employees": len({i["employee_id"] for i in items}),
        "total_bdt": sum(i["amount_due_bdt"] for i in items),
    }


def summary(session: "SimSession") -> dict:
    with session.engine.connect() as conn:
        rows = conn.execute(select(sim_advances.c.status, func.count(), func.sum(sim_advances.c.amount)).group_by(sim_advances.c.status)).all()
        outstanding = sum(remaining(conn, a) for a in conn.execute(select(sim_advances).where(sim_advances.c.status.in_(("open", "carried_over", "pending_write_off")))).all())
    return {
        "by_status": {status: {"count": int(n), "amount_bdt": int(total or 0)} for status, n, total in rows},
        "outstanding_bdt": int(outstanding),
        "fees_income_bdt": session.ledger.balance("fees_income") // 100,
        "loss_provision_bdt": session.ledger.balance("loss_provision") // 100,
    }


def advance_flow(session: "SimSession") -> dict:
    """Advance money only (no wages): paid out, recovered by step, still owed, fees and losses."""
    with session.engine.connect() as conn:
        paid_out = conn.execute(select(func.coalesce(func.sum(sim_advances.c.amount), 0))).scalar_one()
        by_step = dict(conn.execute(select(sim_repayments.c.step, func.sum(sim_repayments.c.amount)).group_by(sim_repayments.c.step)).all())
        open_rows = conn.execute(select(sim_advances).where(sim_advances.c.status.in_(("open", "carried_over", "pending_write_off")))).all()
        outstanding = sum(remaining(conn, a) for a in open_rows)
    return {
        "paid_out_bdt": int(paid_out),
        "recovered_payroll_bdt": int(by_step.get("employer_remittance", 0) + by_step.get("carry_over", 0)),
        "recovered_wallet_bdt": int(by_step.get("wallet_debit", 0)),
        "outstanding_bdt": int(outstanding),
        "fees_bdt": session.ledger.balance("fees_income") // 100,
        "written_off_bdt": session.ledger.balance("loss_provision") // 100,
    }
