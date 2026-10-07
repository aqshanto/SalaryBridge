"""Employer HR endpoints. HR sees one deduction list and one total; never risk scores, tiers or reasons."""

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.integrations import payroll
from app.routers.sim import get_session
from app.sim import settlement
from app.sim.personas import personas_for
from app.sim.session import SessionError, SimSession

router = APIRouter(prefix="/employer", tags=["employer"])


class AttendanceRecord(BaseModel):
    employee_id: str
    unpaid_absent_days: int = Field(ge=0, le=31)
    work_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")  # default: the current simulated month


class AttendanceIn(BaseModel):
    records: list[AttendanceRecord] = Field(min_length=1, max_length=5_000)


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


@router.post("/{employer_id}/attendance")
def attendance_webhook(employer_id: str, body: AttendanceIn, session: SimSession = Depends(get_session)) -> dict:
    """Time-card webhook: the employer's attendance system pushes unpaid absence days; limits update on the next request.

    Only staff of this employer are accepted. Attendance changes earned wages (a rule input), never a model feature.
    """
    _check(session, employer_id)
    staff = session.world.employees
    month_default = session.sim_date.isoformat()[:7]
    accepted, rejected = 0, []
    for r in body.records:
        if r.employee_id not in staff.index or staff.at[r.employee_id, "employer_id"] != employer_id:
            rejected.append(r.employee_id)
            continue
        session.record_attendance(r.employee_id, r.work_month or month_default, r.unpaid_absent_days)
        accepted += 1
    return {"employer_id": employer_id, "accepted": accepted, "rejected": rejected}


@router.post("/{employer_id}/payroll-import")
async def payroll_import(employer_id: str, request: Request, fmt: str = "csv", mapping: str = "generic", session: SimSession = Depends(get_session)) -> dict:
    """Import a payroll or time-card export (CSV or JSON) through a column-mapping adapter.

    Unpaid absence days feed earned days, exactly like the attendance webhook. Salary figures are compared with
    the records upay holds and differences are reported for HR to check; they are never applied silently.
    """
    _check(session, employer_id)
    body = await request.body()
    if len(body) > 5_000_000:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")
    try:
        records = payroll.parse(body.decode("utf-8", errors="strict"), fmt, mapping)
    except (payroll.PayrollFormatError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Payroll file rejected: {exc}") from exc
    staff = session.world.employees
    month = session.sim_date.isoformat()[:7]
    accepted, unknown, salary_mismatch = 0, [], []
    for r in records:
        if r.employee_id not in staff.index or staff.at[r.employee_id, "employer_id"] != employer_id:
            unknown.append(r.employee_id)
            continue
        session.record_attendance(r.employee_id, month, r.unpaid_absent_days)
        accepted += 1
        if r.net_salary_bdt is not None and r.net_salary_bdt != int(staff.at[r.employee_id, "salary_bdt"]):
            salary_mismatch.append(r.employee_id)
    return {"employer_id": employer_id, "format": fmt, "mapping": mapping, "rows": len(records), "accepted": accepted,
            "unknown_staff": unknown[:50], "salary_mismatch_for_review": salary_mismatch[:50]}


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
