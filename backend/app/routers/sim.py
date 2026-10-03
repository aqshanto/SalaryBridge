from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services import scenarios
from app.sim.session import MAX_ADVANCE_DAYS, SessionError, SimSession
from app.sim.world import load_world

router = APIRouter(prefix="/sim", tags=["simulation"])


class AdvanceTimeIn(BaseModel):
    days: int = Field(ge=1, le=MAX_ADVANCE_DAYS)


class JumpToPaydayIn(BaseModel):
    employer_id: str | None = None


class ScenarioIn(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    reset: bool = True


def _session(session_id: str) -> SimSession:
    settings = get_settings()
    try:
        return SimSession(session_id, settings, load_world(settings.database_url))
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_session(x_session_id: str = Header(...)) -> SimSession:
    return _session(x_session_id).open()


@router.post("/reset")
def reset(x_session_id: str = Header(...)) -> dict:
    return _session(x_session_id).reset().state()


@router.get("/state")
def state(session: SimSession = Depends(get_session)) -> dict:
    return session.state()


@router.post("/advance-time")
def advance_time(body: AdvanceTimeIn, session: SimSession = Depends(get_session)) -> dict:
    events = session.advance(body.days)
    return {"state": session.state(), "events": events}


@router.post("/jump-to-payday")
def jump_to_payday(body: JumpToPaydayIn | None = None, session: SimSession = Depends(get_session)) -> dict:
    try:
        target = session.next_payday(body.employer_id if body else None)
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    events = session.advance_to(target)
    return {"state": session.state(), "events": events}


@router.post("/scenario")
def scenario(body: ScenarioIn, session: SimSession = Depends(get_session)) -> dict:
    try:
        return scenarios.run(session, body.name, body.reset)
    except scenarios.ScenarioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
