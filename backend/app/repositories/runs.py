"""Persistence operations for Temporal run snapshots."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import ACTIVE_RUN_STATUSES, RUN_STATUS_VALUES, TERMINAL_RUN_STATUSES, Run


class _Unset:
    """Distinguish an omitted snapshot field from an explicit null value."""


UNSET = _Unset()


def _validate_run_status(status: str) -> None:
    if status not in RUN_STATUS_VALUES:
        valid_values = ", ".join(RUN_STATUS_VALUES)
        raise ValueError(f"Unknown run status {status!r}; expected one of: {valid_values}")


async def create_run(
    session: AsyncSession,
    *,
    order_id: str,
    workflow_id: str,
    supervisor_config_id: UUID,
    order_context: Mapping[str, Any],
    status: str = "starting",
    temporal_run_id: str | None = None,
    current_order_state: Mapping[str, Any] | None = None,
    additional_instructions: Sequence[Any] | None = None,
    memory_summary: Any | None = None,
    final_output: Any | None = None,
    next_wake_at: datetime | None = None,
    started_at: datetime | None = None,
    run_id: UUID | None = None,
) -> Run:
    """Create and flush a run snapshot before starting its Temporal Workflow."""

    _validate_run_status(status)
    values: dict[str, Any] = {
        "id": run_id or uuid4(),
        "order_id": order_id,
        "workflow_id": workflow_id,
        "temporal_run_id": temporal_run_id,
        "supervisor_config_id": supervisor_config_id,
        "status": status,
        "current_order_state": deepcopy(dict(current_order_state or {})),
        "order_context": deepcopy(dict(order_context)),
        "additional_instructions": deepcopy(list(additional_instructions or [])),
        "memory_summary": deepcopy(memory_summary),
        "final_output": deepcopy(final_output),
        "next_wake_at": next_wake_at,
    }
    if started_at is not None:
        values["started_at"] = started_at

    run = Run(**values)
    session.add(run)
    await session.flush()
    return run


async def get_run(session: AsyncSession, run_id: UUID) -> Run | None:
    """Return one persisted run by primary key."""

    return await session.get(Run, run_id)


async def get_active_run_for_order(session: AsyncSession, order_id: str) -> Run | None:
    """Return the active run for an order, if one exists."""

    return await session.scalar(
        select(Run)
        .where(Run.order_id == order_id, Run.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(Run.created_at.desc())
        .limit(1)
    )


async def list_runs(session: AsyncSession, *, status: str | None = None) -> list[Run]:
    """Return runs newest first, optionally filtered by lifecycle status."""

    statement = select(Run)
    if status is not None:
        _validate_run_status(status)
        statement = statement.where(Run.status == status)
    result = await session.scalars(statement.order_by(Run.created_at.desc(), Run.id.desc()))
    return list(result)


async def update_run_state(
    session: AsyncSession,
    run_id: UUID,
    *,
    status: str | _Unset = UNSET,
    temporal_run_id: str | None | _Unset = UNSET,
    current_order_state: Mapping[str, Any] | _Unset = UNSET,
    additional_instructions: Sequence[Any] | _Unset = UNSET,
    memory_summary: Any | _Unset = UNSET,
    final_output: Any | _Unset = UNSET,
    next_wake_at: datetime | None | _Unset = UNSET,
    completion_reason: str | None | _Unset = UNSET,
    completed_at: datetime | None | _Unset = UNSET,
) -> Run | None:
    """Flush selected Workflow-owned snapshot fields without committing."""

    run = await get_run(session, run_id)
    if run is None:
        return None

    if not isinstance(status, _Unset):
        _validate_run_status(status)
        run.status = status
    if not isinstance(temporal_run_id, _Unset):
        run.temporal_run_id = temporal_run_id
    if not isinstance(current_order_state, _Unset):
        run.current_order_state = deepcopy(dict(current_order_state))
    if not isinstance(additional_instructions, _Unset):
        run.additional_instructions = deepcopy(list(additional_instructions))
    if not isinstance(memory_summary, _Unset):
        run.memory_summary = deepcopy(memory_summary)
    if not isinstance(final_output, _Unset):
        run.final_output = deepcopy(final_output)
    if not isinstance(next_wake_at, _Unset):
        run.next_wake_at = next_wake_at
    if not isinstance(completion_reason, _Unset):
        run.completion_reason = completion_reason
    if not isinstance(completed_at, _Unset):
        run.completed_at = completed_at

    await session.flush()
    return run


async def mark_run_completed(
    session: AsyncSession,
    run_id: UUID,
    *,
    status: str,
    completion_reason: str,
    completed_at: datetime | None = None,
    current_order_state: Mapping[str, Any] | _Unset = UNSET,
) -> Run | None:
    """Mark a run terminal and clear its next scheduled wake."""

    if status not in TERMINAL_RUN_STATUSES:
        valid_values = ", ".join(TERMINAL_RUN_STATUSES)
        raise ValueError(f"Completion status must be one of: {valid_values}")
    return await update_run_state(
        session,
        run_id,
        status=status,
        current_order_state=current_order_state,
        next_wake_at=None,
        completion_reason=completion_reason,
        completed_at=completed_at or datetime.now(UTC),
    )


__all__ = [
    "create_run",
    "get_active_run_for_order",
    "get_run",
    "list_runs",
    "mark_run_completed",
    "update_run_state",
]
