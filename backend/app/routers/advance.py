import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.routers.ops import run
from app.routers.sim import get_session
from app.services import advances
from app.llm.explain import explain, facts_for, template
from app.services.decision import DecisionError, decide
from app.sim.personas import personas_for
from app.sim.session import SimSession

router = APIRouter(tags=["advance"])


class OfferIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=40)
    amount_bdt: int = Field(gt=0, le=1_000_000)


@router.get("/personas")
def personas(session: SimSession = Depends(get_session)) -> list[dict]:
    start = session.world.first_live_month.replace(day=get_settings().sim_start_day)
    return [asdict(p) for p in personas_for(get_settings().database_url, start)]


class AcceptIn(BaseModel):
    decision_id: str = Field(min_length=1, max_length=20)


@router.post("/advance/accept")
def accept(body: AcceptIn, session: SimSession = Depends(get_session)) -> dict:
    return run(advances.accept, session, body.decision_id)


@router.post("/advance/offer")
def offer(body: OfferIn, session: SimSession = Depends(get_session)) -> dict:
    try:
        out = decide(session, body.employee_id, body.amount_bdt, get_settings().policy).to_dict()
    except DecisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    facts = facts_for(out, get_settings().policy)
    out["explanation"] = {"en": template(facts, "en"), "bn": template(facts, "bn"), "source": "template", "points": facts["points"]}
    return out


class ExplainIn(BaseModel):
    decision_id: str = Field(min_length=1, max_length=20)


_EXPLAINED: dict[tuple[str, str], dict] = {}  # (session, decision) -> explanation; decisions never change
_MAX_CACHED = 2_000


@router.post("/advance/explain")
def explain_decision(body: ExplainIn, session: SimSession = Depends(get_session)) -> dict:
    """Natural-language wording by Claude when an API key is set; the template otherwise or on any failure."""
    key = (session.session_id, body.decision_id)
    if key not in _EXPLAINED:
        row = session.decision(body.decision_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown decision: {body.decision_id}")
        output = json.loads(row["payload"])["output"]
        if len(_EXPLAINED) >= _MAX_CACHED:
            _EXPLAINED.clear()
        _EXPLAINED[key] = explain(output, get_settings()).to_dict()
    return {"decision_id": body.decision_id, **_EXPLAINED[key]}
