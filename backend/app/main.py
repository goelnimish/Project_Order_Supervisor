"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.database import engine
from app.routers.analytics import router as analytics_router
from app.routers.health import router as health_router
from app.routers.runs import router as runs_router
from app.routers.supervisors import router as supervisors_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Release local database resources during application shutdown."""

    yield
    await engine.dispose()


app = FastAPI(
    title="Order Supervisor API",
    description=(
        "Local Order Supervisor control plane. Temporal owns orchestration; PostgreSQL is the "
        "eventually consistent operational read model and audit trail. Interrupt "
        "enters a human-review state until resume or graceful termination."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return useful validation locations without echoing rejected request values."""

    safe_errors = [
        {
            "type": error["type"],
            "loc": error["loc"],
            "msg": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": safe_errors},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(supervisors_router)
app.include_router(runs_router)
app.include_router(analytics_router)
