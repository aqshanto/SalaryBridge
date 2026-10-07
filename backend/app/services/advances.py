"""Turning decisions into advances: employee acceptance, the human approval queue and the kill switch.

Money moves only here (payout) and in settlement (F14). A payout posts one balanced ledger entry:
upay_pool -> employee_wallet. The amount owed (principal + fee) is tracked on the advance record and
collected on payday.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import insert, select, update

from app.ledger import bdt_to_paisa, paisa_to_bdt
from app.rules import fee_for
from app.sim.session import SimSession, sim_advances, sim_decisions, sim_reviews

MIN_NOTE_LENGTH = 5


class AdvanceError(ValueError):
    """Bad input (400)."""


class ConflictError(ValueError):
    """The request is valid but the decision is not in a state that allows it (409)."""


def _load(session: SimSession, decision_id: str) -> tuple[dict, dict]:
    row = session.decision(decision_id)
    if row is None:
        raise AdvanceError(f"Unknown decision: {decision_id}")
    if row["review"] is not None:
        raise ConflictError(f"Decision {decision_id} was already {row['review']['action']}")
    return row, json.loads(row["payload"])["output"]


def _check_fresh(session: SimSession, row: dict) -> None:
    if session.kill_switch:
        raise ConflictError("New advances are paused by operations (kill switch)")
    if row["sim_date"] != session.sim_date:
        raise ConflictError("This offer has expired: the simulation date has moved on. Ask for a new offer.")
    with session.engine.connect() as conn:
        newer = conn.execute(
            select(sim_advances.c.advance_id).where(
                sim_advances.c.employee_id == row["employee_id"], sim_advances.c.issue_date >= row["sim_date"]
            )
        ).first()
    if newer is not None:
        raise ConflictError("Another advance was paid to this person after this offer was made. Ask for a new offer.")


def _pay_out(session: SimSession, output: dict, amount: int, action: str, actor: str, note: str | None) -> dict:
    today = session.sim_date
    fee = fee_for(amount, session.settings.policy)
    advance_id = session.next_id(sim_advances, "L")
    wallet = f"employee_wallet:{output['employee_id']}"
    session.ledger.post_entry(
        today,
        "advance_payout",
        [(wallet, bdt_to_paisa(amount)), ("upay_pool", -bdt_to_paisa(amount))],
        ref=advance_id,
        memo=f"Advance for {output['employee_id']} ({output['decision_id']})",
    )
    record = {
        "advance_id": advance_id,
        "decision_id": output["decision_id"],
        "employee_id": output["employee_id"],
        "employer_id": output["employer_id"],
        "work_month": today.isoformat()[:7],
        "issue_date": today,
        "amount_requested": output["requested_bdt"],
        "hard_cap": output["hard_cap_bdt"],
        "amount": amount,
        "fee": fee,
        "due_date": date.fromisoformat(output["repayment_date"]),
        "grace_end": date.fromisoformat(output["grace_end"]),
        "tier": output["tier"] or "-",
        "status": "open",
        "recovered_by_grace": None,
        "settled_date": None,
    }
    with session.engine.begin() as conn:
        conn.execute(insert(sim_advances).values(**record))
        conn.execute(
            insert(sim_reviews).values(
                decision_id=output["decision_id"], action=action, actor=actor, note=note, sim_date=today, amount_bdt=amount, advance_id=advance_id
            )
        )
    return {
        "advance_id": advance_id,
        "decision_id": output["decision_id"],
        "employee_id": output["employee_id"],
        "amount_bdt": amount,
        "fee_bdt": fee,
        "total_due_bdt": amount + fee,
        "due_date": record["due_date"].isoformat(),
        "grace_end": record["grace_end"].isoformat(),
        "wallet_balance_bdt": paisa_to_bdt(session.ledger.balance(wallet)),
        "pool_bdt": paisa_to_bdt(session.ledger.balance("upay_pool")),
        "ledger_reconciled": session.ledger.reconcile().ok,
    }


def accept(session: SimSession, decision_id: str) -> dict:
    """The employee accepts an automatic offer."""
    row, output = _load(session, decision_id)
    if row["status"] != "offered":
        raise ConflictError(f"Only offered decisions can be accepted; this one is {row['status']}")
    _check_fresh(session, row)
    if session.settings.policy.employer_confirmation_required:
        # The employee has accepted; upay pays out only after the employer's HR confirms (employer_decide).
        with session.engine.begin() as conn:
            conn.execute(update(sim_decisions).where(sim_decisions.c.decision_id == decision_id).values(status="awaiting"))
        return {
            "status": "awaiting_employer",
            "decision_id": decision_id,
            "employee_id": output["employee_id"],
            "amount_bdt": int(output["approved_amount_bdt"]),
            "fee_bdt": fee_for(int(output["approved_amount_bdt"]), session.settings.policy),
            "total_due_bdt": int(output["approved_amount_bdt"]) + fee_for(int(output["approved_amount_bdt"]), session.settings.policy),
            "due_date": output["repayment_date"],
            "wallet_balance_bdt": paisa_to_bdt(session.ledger.balance(f"employee_wallet:{output['employee_id']}")),
        }
    return _pay_out(session, output, int(output["approved_amount_bdt"]), "accepted", "employee", None)


def awaiting_for_employer(session: SimSession, employer_id: str) -> list[dict]:
    """Accepted advances waiting for this employer's HR confirmation (no risk information)."""
    with session.engine.connect() as conn:
        rows = conn.execute(select(sim_decisions).where(sim_decisions.c.status == "awaiting").order_by(sim_decisions.c.decision_id)).all()
    out = []
    for r in rows:
        output = json.loads(r.payload)["output"]
        if output["employer_id"] != employer_id:
            continue
        amount = int(output["approved_amount_bdt"])
        fee = fee_for(amount, session.settings.policy)
        out.append({"decision_id": r.decision_id, "employee_id": r.employee_id, "requested_on": r.sim_date.isoformat(), "amount_bdt": amount, "fee_bdt": fee, "total_due_bdt": amount + fee, "repayment_date": output["repayment_date"]})
    return out


def employer_decide(session: SimSession, employer_id: str, decision_id: str, confirm: bool) -> dict:
    """HR confirms (upay pays out to the wallet) or declines an accepted advance."""
    row, output = _load(session, decision_id)
    if row["status"] != "awaiting" or output["employer_id"] != employer_id:
        raise ConflictError("This advance is not waiting for this employer's confirmation")
    if not confirm:
        with session.engine.begin() as conn:
            conn.execute(update(sim_decisions).where(sim_decisions.c.decision_id == decision_id).values(status="hr_declined"))
        return {"decision_id": decision_id, "status": "hr_declined"}
    return {"status": "paid", **_pay_out(session, output, int(output["approved_amount_bdt"]), "employer_confirmed", "employer", None)}


def _note(note: str | None) -> str:
    note = (note or "").strip()
    if len(note) < MIN_NOTE_LENGTH:
        raise AdvanceError(f"A note of at least {MIN_NOTE_LENGTH} characters is required")
    return note


def approve(session: SimSession, decision_id: str, note: str | None, amount_bdt: int | None = None) -> dict:
    """An ops analyst approves a queued request (optionally a different amount, never above the hard cap)."""
    note = _note(note)
    row, output = _load(session, decision_id)
    if row["status"] != "queued":
        raise ConflictError(f"Only queued decisions can be approved; this one is {row['status']}")
    amount = int(amount_bdt if amount_bdt is not None else output["approved_amount_bdt"])
    policy = session.settings.policy
    if not policy.min_advance_bdt <= amount <= output["hard_cap_bdt"]:
        raise AdvanceError(f"Amount must be between {policy.min_advance_bdt} and the hard cap {output['hard_cap_bdt']} BDT")
    _check_fresh(session, row)
    return _pay_out(session, output, amount, "approved", "ops", note)


def reject(session: SimSession, decision_id: str, note: str | None) -> dict:
    note = _note(note)
    row, output = _load(session, decision_id)
    if row["status"] != "queued":
        raise ConflictError(f"Only queued decisions can be rejected; this one is {row['status']}")
    with session.engine.begin() as conn:
        conn.execute(
            insert(sim_reviews).values(decision_id=decision_id, action="rejected", actor="ops", note=note, sim_date=session.sim_date, amount_bdt=0, advance_id=None)
        )
    return {"decision_id": decision_id, "action": "rejected", "note": note}


def queue(session: SimSession) -> list[dict]:
    """Queued decisions nobody has resolved yet, newest first, with the evidence an analyst needs."""
    with session.engine.connect() as conn:
        rows = conn.execute(
            select(sim_decisions)
            .where(sim_decisions.c.status == "queued", sim_decisions.c.decision_id.not_in(select(sim_reviews.c.decision_id)))
            .order_by(sim_decisions.c.decision_id.desc())
        ).mappings().all()
    out = []
    for row in rows:
        output = json.loads(row["payload"])["output"]
        out.append(
            {
                "decision_id": row["decision_id"],
                "sim_date": row["sim_date"].isoformat(),
                "employee_id": row["employee_id"],
                "employer_id": output["employer_id"],
                "requested_bdt": output["requested_bdt"],
                "proposed_bdt": output["approved_amount_bdt"],
                "hard_cap_bdt": output["hard_cap_bdt"],
                "tier": output["tier"],
                "risk": output["risk"],
                "reasons": output["reasons"],
                "expired": row["sim_date"] != session.sim_date,
            }
        )
    return out


def review_log(session: SimSession) -> list[dict]:
    with session.engine.connect() as conn:
        rows = conn.execute(select(sim_reviews).order_by(sim_reviews.c.sim_date, sim_reviews.c.decision_id)).mappings().all()
    return [{**dict(r), "sim_date": r["sim_date"].isoformat()} for r in rows]
