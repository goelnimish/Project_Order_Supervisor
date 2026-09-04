"""Injectable dependencies shared by Stage 2 API routers."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from app.database import get_session
from app.temporal_client import connect_temporal


async def get_temporal_client() -> Client:
    """Connect on demand and hide transport details from API consumers."""

    try:
        return await connect_temporal()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_unavailable",
                "message": "Temporal is currently unavailable.",
            },
        ) from exc


async def get_optional_temporal_client() -> Client | None:
    """Return a live client when available without hiding PostgreSQL read data."""

    try:
        return await connect_temporal()
    except Exception:
        return None


DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
TemporalClient = Annotated[Client, Depends(get_temporal_client)]
OptionalTemporalClient = Annotated[Client | None, Depends(get_optional_temporal_client)]
