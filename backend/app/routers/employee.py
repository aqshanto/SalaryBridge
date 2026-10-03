import calendar

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from app.config import get_settings
from app.ledger import paisa_to_bdt
from app.llm.explain import facts_for, template
from app.routers.sim import get_session
from app.services.decision import decide
from app.sim.session import SimSession

router = APIRouter(prefix="/employee", tags=["employee"])

SEED_HISTORY_ROWS = 6
STEP_LABELS = {"employer_remittance": "payday", "wallet_debit": "wallet", "carry_over": "next_payday", "write_off": "written_off"}


def _history(session: SimSession, employee_id: str) -> list[dict]:
    """This session's advances (newest first), then the last few from the seed history."""
    out = []
    live = session.advances_df(employee_id)
    for adv in live.sort_values("issue_date", ascending=False).itertuples():
        steps = session.repayments_df(adv.advance_id)
        out.append(
            {
                "advance_id": adv.advance_id,
                "issue_date": str(adv.issue_date),
                "amount_bdt": int(adv.amount),
                "fee_bdt": int(adv.fee),
                "due_date": str(adv.due_date),
                "status": adv.status,
                "settled_via": [STEP_LABELS.get(s, s) for s in steps["step"]] if len(steps) else [],
                "on_time": None if pd.isna(adv.recovered_by_grace) else bool(adv.recovered_by_grace),
                "source": "live",
            }
        )
    seed = session.world.advances[session.world.advances["employee_id"] == employee_id]
    for adv in seed.sort_values("issue_date", ascending=False).head(SEED_HISTORY_ROWS).itertuples():
        out.append(
            {
                "advance_id": adv.advance_id,
                "issue_date": str(adv.issue_date)[:10],
                "amount_bdt": int(adv.amount),
                "fee_bdt": int(adv.fee),
                "due_date": str(adv.due_date)[:10],
                "status": "written_off" if adv.final_step == "write_off" else "recovered",
                "settled_via": [STEP_LABELS.get(adv.final_step, adv.final_step)],
                "on_time": bool(adv.recovered_by_grace),
                "source": "history",
            }
        )
    return out


@router.get("/{employee_id}/summary")
def summary(employee_id: str, session: SimSession = Depends(get_session)) -> dict:
    """What the employee's home screen needs. Uses a preview decision, which is not logged."""
    world = session.world
    if employee_id not in world.employees.index:
        raise HTTPException(status_code=404, detail=f"Unknown or inactive employee: {employee_id}")
    policy = get_settings().policy
    person = world.employees.loc[employee_id]
    employer = world.employers_all.set_index("employer_id").loc[person["employer_id"]]
    today = session.sim_date
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    salary = int(person["salary_bdt"])
    salary_cap = int(salary * policy.cap_pct_of_salary / 100)

    preview = decide(session, employee_id, salary_cap, policy, preview=True).to_dict()
    facts = facts_for(preview, policy)
    return {
        "employee_id": employee_id,
        "employer_id": person["employer_id"],
        "employer_name": employer["name"],
        "salary_bdt": salary,
        "salary_to_upay": bool(person["salary_to_upay"]),
        "days_worked": today.day,
        "days_in_month": days_in_month,
        "earned_to_date_bdt": int(salary * today.day / days_in_month),
        "available_bdt": preview["max_amount_bdt"] if preview["status"] != "declined" else 0,
        "hard_cap_bdt": preview["hard_cap_bdt"],
        "min_advance_bdt": policy.min_advance_bdt,
        "fee_bdt": policy.fee_flat_bdt,
        "next_deduction_date": preview["repayment_date"],
        "preview_status": preview["status"],
        "preview_message": {"en": template(facts, "en"), "bn": template(facts, "bn")} if preview["status"] == "declined" else None,
        "wallet_bdt": paisa_to_bdt(session.ledger.balance(f"employee_wallet:{employee_id}")),
        "active": session.resignation(employee_id) is None,
        "history": _history(session, employee_id),
    }
