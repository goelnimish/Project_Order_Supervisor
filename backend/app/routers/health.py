"""Service and dependency health endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.database import database_is_reachable
from app.schemas.health import DatabaseHealthResponse, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> HealthResponse:
    """Report that the API process is healthy."""

    return HealthResponse(status="healthy", service="order-supervisor-api")


@router.get(
    "/health/database",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": DatabaseHealthResponse}},
)
async def database_health(
    response: Response,
    reachable: Annotated[bool, Depends(database_is_reachable)],
) -> DatabaseHealthResponse:
    """Report PostgreSQL reachability without exposing credentials."""

    is_healthy = bool(reachable)
    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DatabaseHealthResponse(
        status="healthy" if is_healthy else "unhealthy",
        service="postgresql",
        reachable=is_healthy,
    )
