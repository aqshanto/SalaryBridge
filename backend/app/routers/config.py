from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/public")
def public_config() -> dict:
    """Non-secret settings: the seed and every policy parameter."""
    settings = get_settings()
    return {"seed": settings.seed, "policy": settings.policy.model_dump(), "llm_enabled": bool(settings.anthropic_api_key)}
