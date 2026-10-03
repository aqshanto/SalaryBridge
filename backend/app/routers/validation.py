import json

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.ml.common import ARTIFACTS_DIR

router = APIRouter(tags=["validation"])


@router.get("/validation")
def validation() -> dict:
    """The validation results produced by `python -m scripts.validate` (not computed per request)."""
    path = ARTIFACTS_DIR / "validation.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run `python -m scripts.validate` to generate validation results")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["policy"] = get_settings().policy.model_dump()  # the assumption values the sliders start from
    return data
