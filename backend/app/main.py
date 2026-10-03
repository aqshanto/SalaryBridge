from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import APP_VERSION, get_settings
from app.routers import advance as advance_router
from app.routers import config as config_router
from app.routers import employee as employee_router
from app.routers import employer as employer_router
from app.routers import ops as ops_router
from app.routers import sim as sim_router
from app.routers import validation as validation_router
from data.seed import ensure_seed_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    ensure_seed_db(settings.database_url, seed=settings.seed)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SalaryBridge API", version=APP_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(config_router.router)
    app.include_router(sim_router.router)
    app.include_router(advance_router.router)
    app.include_router(ops_router.router)
    app.include_router(employer_router.router)
    app.include_router(employee_router.router)
    app.include_router(validation_router.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": APP_VERSION, "seed": settings.seed}

    return app


app = create_app()
