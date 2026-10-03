import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.routers.sim import get_session
from app.sim import settlement
from app.sim.session import SimSession

router = APIRouter(prefix="/employer", tags=["employer"])


def _notice(session: SimSession, employer_id: str) -> dict:
    if employer_id not in session.world.employers_all["employer_id"].values:
        raise HTTPException(status_code=404, detail=f"Unknown employer: {employer_id}")
    return settlement.deduction_notice(session, employer_id)


@router.get("/{employer_id}/deduction-notice")
def deduction_notice(employer_id: str, session: SimSession = Depends(get_session)) -> dict:
    """One list and one total for HR. Contains no risk scores."""
    return _notice(session, employer_id)


@router.get("/{employer_id}/deduction-notice.csv")
def deduction_notice_csv(employer_id: str, session: SimSession = Depends(get_session)) -> Response:
    notice = _notice(session, employer_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["employee_id", "advance_id", "kind", "issue_date", "amount_due_bdt"])
    for item in notice["items"]:
        writer.writerow([item["employee_id"], item["advance_id"], item["kind"], item["issue_date"], item["amount_due_bdt"]])
    writer.writerow(["TOTAL", "", "", "", notice["total_bdt"]])
    filename = f"deduction-notice-{employer_id}-{notice['payday'] or 'none'}.csv"
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
