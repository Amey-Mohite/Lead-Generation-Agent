from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "version": settings.app_version}


@router.get("/ready")
def ready() -> JSONResponse:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "database": "down"}
        )
    return JSONResponse(status_code=200, content={"status": "ready", "database": "up"})
