from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.database.init_db import initialize_database


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Autonomous project research and development agent.",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "status": "running",
        "environment": settings.app_env,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "database": "configured",
        "agent": settings.app_name,
    }