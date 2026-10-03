from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.routers.sim import get_session
from app.services import advances
from app.sim.session import SimSession

router = APIRouter(prefix="/ops", tags=["ops"])


class ReviewIn(BaseModel):
    note: str = Field(default="", max_length=500)
    amount_bdt: int | None = Field(default=None, gt=0)


class KillSwitchIn(BaseModel):
    on: bool


def run(fn, *args):
    try:
        return fn(*args)
    except advances.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except advances.AdvanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/queue")
def queue(session: SimSession = Depends(get_session)) -> list[dict]:
    return advances.queue(session)


@router.post("/queue/{decision_id}/approve")
def approve(decision_id: str, body: ReviewIn, session: SimSession = Depends(get_session)) -> dict:
    return run(advances.approve, session, decision_id, body.note, body.amount_bdt)


@router.post("/queue/{decision_id}/reject")
def reject(decision_id: str, body: ReviewIn, session: SimSession = Depends(get_session)) -> dict:
    return run(advances.reject, session, decision_id, body.note)


@router.get("/reviews")
def reviews(session: SimSession = Depends(get_session)) -> list[dict]:
    return advances.review_log(session)


@router.post("/kill-switch")
def kill_switch(body: KillSwitchIn, session: SimSession = Depends(get_session)) -> dict:
    session.set_kill_switch(body.on)
    return {"kill_switch": session.kill_switch}
