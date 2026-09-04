"""PostgreSQL persistence Activities for Workflow-owned state transitions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.database import async_session_factory
from app.repositories.activities import append_activity_idempotently
from app.repositories.runs import update_run_state
from app.temporal.models import PersistenceTransitionRequest

PERSIST_TRANSITION_ACTIVITY_NAME = "persist_workflow_transition"


@activity.defn(name=PERSIST_TRANSITION_ACTIVITY_NAME)
async def persist_workflow_transition(request: PersistenceTransitionRequest) -> None:
    """Append one audit row and advance its run snapshot in one transaction."""

    try:
        run_id = UUID(request.run_id)
    except ValueError:
        raise ApplicationError(
            "Persistence transition contains an invalid run ID.",
            type="InvalidPersistenceRunId",
            non_retryable=True,
        ) from None

    async with async_session_factory.begin() as session:
        _, created = await append_activity_idempotently(
            session,
            run_id=run_id,
            activity_key=request.activity_key,
            activity_type=request.activity_type,
            source=request.source,
            event_name=request.event_name,
            action_name=request.action_name,
            status=request.status,
            summary=request.summary,
            payload=request.payload,
            external_event_id=request.external_event_id,
            created_at=_parse_datetime(request.recorded_at),
        )
        if not created:
            return

        updated_run = await update_run_state(
            session,
            run_id,
            status=request.workflow_status.value,
            temporal_run_id=request.temporal_run_id,
            current_order_state=request.current_order_state,
            additional_instructions=request.additional_instructions,
            memory_summary=request.memory_summary,
            final_output=request.final_output,
            next_wake_at=_parse_optional_datetime(request.next_wake_at),
            completion_reason=request.completion_reason,
            completed_at=_parse_optional_datetime(request.completed_at),
        )
        if updated_run is None:
            raise ApplicationError(
                "Persistence transition references an unknown run.",
                type="PersistenceRunNotFound",
                non_retryable=True,
            )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return _parse_datetime(value) if value is not None else None
