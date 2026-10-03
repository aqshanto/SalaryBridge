"""Employer HR endpoints. HR sees one deduction list and one total; never risk scores, tiers or reasons."""

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.routers.sim import get_session
from app.sim import settlement
from app.sim.personas import personas_for
from app.sim.session import SessionError, SimSession

router = APIRouter(prefix="/employer", tags=["employer"])


class SettingsIn(BaseModel):
    opted_in: bool
    cap_pct: float = Field(gt=0, le=100)


def _check(session: SimSession, employer_id: str) -> None:
    if employer_id not in session.world.employers_all["employer_id"].values:
        raise HTTPException(status_code=404, detail=f"Unknown employer: {employer_id}")


def _names(session: SimSession) -> dict[str, str]:
    """Demo names for the persona employees; everyone else is shown by staff ID."""
    start = session.world.first_live_month.replace(day=session.settings.sim_start_day)
    return {p.employee_id: p.name for p in personas_for(get_settings().database_url, start)}


def _notice(session: SimSession, employer_id: str) -> dict:
    _check(session, employer_id)
    notice = settlement.deduction_notice(session, employer_id)
    names = _names(session)
    for item in notice["items"]:
        item["name"] = names.get(item["employee_id"], item["employee_id"])
    notice["confirmation"] = session.notice_ack(employer_id, date.fromisoformat(notice["payday"])) if notice["payday"] else None
    return notice


@router.get("")
def employers(session: SimSession = Depends(get_session)) -> list[dict]:
    """Employers HR can sign in as in the demo (persona employers first)."""
    start = session.world.first_live_month.replace(day=session.settings.sim_start_day)
    persona_of = {p.employer_id: p.name for p in personas_for(get_settings().database_url, start)}
    closed = session.closed_employers()
    rows = [
        {
            "employer_id": eid,
            "name": e["name"],
            "industry": e["industry"],
            "headcount": int(e["headcount"]),
            "payroll_day": int(e["payroll_day"]),
            "status": "closed" if eid in closed else "open",
            "persona": persona_of.get(eid),
        }
        for eid, e in session.world.employers.iterrows()
    ]
    return sorted(rows, key=lambda r: (r["persona"] is None, r["employer_id"]))


@router.get("/{employer_id}/dashboard")
def dashboard(employer_id: str, session: SimSession = Depends(get_session)) -> dict:
    notice = _notice(session, employer_id)
    employer = session.world.employers_all.set_index("employer_id").loc[employer_id]
    live = session.advances_df()
    mine = live[live["employer_id"] == employer_id] if not live.empty else live
    recovered = mine[mine["status"] == "recovered"] if not mine.empty else mine
    return {
        "employer_id": employer_id,
        "name": employer["name"],
        "headcount": int(employer["headcount"]),
        "payroll_day": int(employer["payroll_day"]),
        "status": "closed" if employer_id in session.closed_employers() else "open",
        "settings": session.employer_settings(employer_id),
        "upay_cap_pct": session.settings.policy.cap_pct_of_salary,
        "payday": notice["payday"],
        "employees_with_deductions": notice["employees"],
        "total_to_deduct_bdt": notice["total_bdt"],
        "advances_settled_this_session": int(len(recovered)),
        "remitted_this_session_bdt": int((recovered["amount"] + recovered["fee"]).sum()) if len(recovered) else 0,
        "notice": notice,
    }


@router.get("/{employer_id}/deduction-notice")
def deduction_notice(employer_id: str, session: SimSession = Depends(get_session)) -> dict:
    return _notice(session, employer_id)


@router.post("/{employer_id}/deduction-notice/confirm")
def confirm_notice(employer_id: str, session: SimSession = Depends(get_session)) -> dict:
    """HR confirms it will remit the total on payday. Money still moves only on payday (simulated)."""
    notice = _notice(session, employer_id)
    if not notice["payday"] or not notice["items"]:
        raise HTTPException(status_code=409, detail="There is nothing to remit on the next payday")
    session.ack_notice(employer_id, date.fromisoformat(notice["payday"]), notice["total_bdt"])
    return _notice(session, employer_id)


@router.get("/{employer_id}/deduction-notice.csv")
def deduction_notice_csv(employer_id: str, session: SimSession = Depends(get_session)) -> Response:
    notice = _notice(session, employer_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["employee_id", "name", "advance_id", "kind", "issue_date", "amount_due_bdt"])
    for item in notice["items"]:
        writer.writerow([item["employee_id"], item["name"], item["advance_id"], item["kind"], item["issue_date"], item["amount_due_bdt"]])
    writer.writerow(["TOTAL", "", "", "", "", notice["total_bdt"]])
    filename = f"deduction-notice-{employer_id}-{notice['payday'] or 'none'}.csv"
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{employer_id}/settings")
def get_settings_(employer_id: str, session: SimSession = Depends(get_session)) -> dict:
    _check(session, employer_id)
    return session.employer_settings(employer_id)


@router.put("/{employer_id}/settings")
def put_settings(employer_id: str, body: SettingsIn, session: SimSession = Depends(get_session)) -> dict:
    _check(session, employer_id)
    try:
        return session.set_employer_settings(employer_id, body.opted_in, body.cap_pct)
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
