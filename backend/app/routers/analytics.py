"""Read-only PostgreSQL analytics endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.analytics.schemas import GlobalAnalyticsResponse, RunAnalyticsResponse
from app.analytics.service import get_global_analytics as load_global_analytics
from app.analytics.service import get_run_analytics as load_run_analytics
from app.routers.dependencies import DatabaseSession

router = APIRouter(tags=["analytics"])


def _database_unavailable() -> HTTPException:
    """Return the application's sanitized PostgreSQL error contract."""

    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "database_unavailable",
            "message": "PostgreSQL is currently unavailable.",
        },
    )


@router.get("/api/analytics/summary", response_model=GlobalAnalyticsResponse)
async def get_analytics_summary(session: DatabaseSession) -> GlobalAnalyticsResponse:
    """Return aggregate operator metrics from the PostgreSQL read model."""

    try:
        return await load_global_analytics(session)
    except SQLAlchemyError as exc:
        raise _database_unavailable() from exc


@router.get("/api/runs/{run_id}/analytics", response_model=RunAnalyticsResponse)
async def get_run_analytics(
    run_id: UUID,
    session: DatabaseSession,
) -> RunAnalyticsResponse:
    """Return persisted analytics for one run without contacting Temporal."""

    try:
        analytics = await load_run_analytics(session, run_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable() from exc

    if analytics is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "run_not_found",
                "message": "Supervisor run was not found.",
            },
        )
    return analytics
