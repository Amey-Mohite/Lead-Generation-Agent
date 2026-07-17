import time
import uuid

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.leads import router as leads_router
from app.config import get_settings
from app.observability.logging_config import request_id_var
from app.observability.metrics import record_request
from app.observability.setup import setup_observability


def create_app() -> FastAPI:
    settings = get_settings()
    setup_observability(settings)
    app = FastAPI(title=settings.app_name, version=settings.app_version)

    @app.middleware("http")
    async def add_request_id(request, call_next):
        token = request_id_var.set(str(uuid.uuid4()))
        try:
            return await call_next(request)
        finally:
            request_id_var.reset(token)

    @app.middleware("http")
    async def record_request_metrics(request, call_next):
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", None) or request.url.path
            record_request(
                method=request.method,
                path=path,
                status=status_code,
                duration_seconds=time.perf_counter() - start,
            )

    app.include_router(health_router)
    app.include_router(leads_router)
    return app


app = create_app()
