"""Integration coverage for retry-safe Temporal persistence Activities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.database import async_session_factory
from app.models.activity import Activity
from app.models.run import Run
from app.models.supervisor import SupervisorConfig
from app.repositories.runs import create_run
from app.repositories.supervisors import create_supervisor
from app.temporal.models import PersistenceTransitionRequest, WorkflowStatus
from app.temporal.persistence_activities import persist_workflow_transition


@pytest.mark.asyncio
async def test_persistence_activity_retry_does_not_duplicate_or_regress_snapshot() -> None:
    """A late retry keeps one row and cannot overwrite a newer run snapshot."""

    token = uuid4().hex
    order_id = f"persistence-activity-{token}"
    workflow_id = f"order-supervisor:{order_id}"
    recorded_at = datetime.now(UTC)

    async with async_session_factory.begin() as session:
        supervisor = await create_supervisor(
            session,
            name=f"Persistence activity supervisor {token}",
            base_instruction="Exercise one idempotent persistence transition.",
            available_actions=["create_internal_note"],
            default_wake_seconds=60,
        )
        run = await create_run(
            session,
            order_id=order_id,
            workflow_id=workflow_id,
            supervisor_config_id=supervisor.id,
            order_context={"order_id": order_id},
        )
        run_id = run.id
        supervisor_id = supervisor.id

    request = PersistenceTransitionRequest(
        run_id=str(run_id),
        workflow_id=workflow_id,
        temporal_run_id=f"temporal-{token}",
        activity_key=f"{workflow_id}:{run_id}:00000001",
        sequence=1,
        recorded_at=recorded_at.isoformat(),
        activity_type="workflow_started",
        source="workflow",
        status="recorded",
        summary="Workflow started for persistence retry coverage.",
        payload={"test_token": token},
        event_name=None,
        action_name=None,
        external_event_id=None,
        workflow_status=WorkflowStatus.SLEEPING,
        current_order_state={"lifecycle": "open"},
        additional_instructions=[],
        next_wake_at=(recorded_at + timedelta(seconds=60)).isoformat(),
        completion_reason=None,
        completed_at=None,
    )
    newer_request = replace(
        request,
        activity_key=f"{workflow_id}:{run_id}:00000002",
        sequence=2,
        recorded_at=(recorded_at + timedelta(seconds=1)).isoformat(),
        activity_type="workflow_paused",
        summary="Workflow paused after the initial transition.",
        workflow_status=WorkflowStatus.PAUSED,
        current_order_state={"lifecycle": "open", "snapshot": "newer"},
    )

    try:
        await persist_workflow_transition(request)
        await persist_workflow_transition(newer_request)
        await persist_workflow_transition(request)

        async with async_session_factory() as session:
            row_count = await session.scalar(
                select(func.count(Activity.id)).where(Activity.run_id == run_id)
            )
            persisted_run = await session.get(Run, run_id)

        assert row_count == 2
        assert persisted_run is not None
        assert persisted_run.status == WorkflowStatus.PAUSED.value
        assert persisted_run.temporal_run_id == request.temporal_run_id
        assert persisted_run.current_order_state == {"lifecycle": "open", "snapshot": "newer"}
        assert persisted_run.next_wake_at is not None
    finally:
        async with async_session_factory.begin() as session:
            await session.execute(delete(Run).where(Run.id == run_id))
            await session.execute(
                delete(SupervisorConfig).where(SupervisorConfig.id == supervisor_id)
            )
