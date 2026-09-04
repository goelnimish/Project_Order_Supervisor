"""Persistence operations for the unified activity timeline."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity


async def append_activity_idempotently(
    session: AsyncSession,
    *,
    run_id: UUID,
    activity_key: str,
    activity_type: str,
    source: str,
    status: str,
    summary: str,
    payload: Mapping[str, Any] | None = None,
    event_name: str | None = None,
    action_name: str | None = None,
    external_event_id: str | None = None,
    activity_id: UUID | None = None,
    created_at: datetime | None = None,
) -> tuple[Activity, bool]:
    """Append one timeline record, returning the existing row on a retry."""

    values: dict[str, Any] = {
        "id": activity_id or uuid4(),
        "run_id": run_id,
        "activity_key": activity_key,
        "activity_type": activity_type,
        "source": source,
        "event_name": event_name,
        "action_name": action_name,
        "status": status,
        "summary": summary,
        "payload": deepcopy(dict(payload or {})),
        "external_event_id": external_event_id,
    }
    if created_at is not None:
        values["created_at"] = created_at

    statement = (
        postgresql_insert(Activity)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Activity.activity_key])
        .returning(Activity)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    await session.flush()
    if inserted is not None:
        return inserted, True

    existing = await session.scalar(select(Activity).where(Activity.activity_key == activity_key))
    if existing is None:
        raise RuntimeError("Conflicting activity row could not be loaded")
    if existing.run_id != run_id:
        raise ValueError("activity_key already belongs to a different run")
    return existing, False


async def list_activities_for_run(session: AsyncSession, run_id: UUID) -> list[Activity]:
    """Return a run's persistent timeline in chronological insertion order."""

    result = await session.scalars(
        select(Activity)
        .where(Activity.run_id == run_id)
        .order_by(Activity.created_at, Activity.activity_key)
    )
    return list(result)


__all__ = ["append_activity_idempotently", "list_activities_for_run"]
