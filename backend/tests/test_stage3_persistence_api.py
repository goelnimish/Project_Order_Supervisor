"""Stage 3 integration coverage for memory, final-output persistence, and API schemas."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, inspect, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from app.database import async_session_factory, engine
from app.models.run import Run
from app.models.supervisor import SupervisorConfig
from app.repositories.runs import create_run
from app.repositories.supervisors import create_supervisor
from app.schemas.runs import RunDetailResponse, RunResponse
from app.temporal.models import PersistenceTransitionRequest, WorkflowStatus
from app.temporal.persistence_activities import persist_workflow_transition

COMPACT_MEMORY = {
    "order_state": "Shipment delayed after payment confirmation.",
    "important_facts": ["Carrier reported a six-hour delay."],
    "open_issues": ["Confirm the revised delivery estimate."],
    "actions_taken": ["Logistics team was notified."],
    "active_constraints": ["Do not promise an unconfirmed delivery time."],
    "next_review": "Review after the carrier update.",
}

FINAL_OUTPUT = {
    "final_summary": "The order was supervised through its terminal state.",
    "important_actions": ["Notified the logistics team about the delay."],
    "key_learnings": ["Early carrier escalation reduced uncertainty."],
    "recommendations": ["Retain proactive delay monitoring."],
}


@dataclass(frozen=True)
class PersistedStage3Run:
    """Identifiers for one committed run used by cross-session Activities."""

    run_id: UUID
    supervisor_id: UUID
    workflow_id: str


@pytest_asyncio.fixture(autouse=True)
async def isolate_application_engine_pool() -> AsyncIterator[None]:
    """Prevent pooled asyncpg connections from crossing per-test event loops."""

    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def stage3_run() -> AsyncIterator[PersistedStage3Run]:
    """Create committed records and remove them after Activity-level assertions."""

    token = uuid4().hex
    order_id = f"stage3-persistence-{token}"
    workflow_id = f"order-supervisor:{order_id}"

    async with async_session_factory.begin() as session:
        supervisor = await create_supervisor(
            session,
            name=f"Stage 3 persistence supervisor {token}",
            base_instruction="Persist compact memory and a final report safely.",
            available_actions=["create_internal_note"],
            default_wake_seconds=60,
        )
        run = await create_run(
            session,
            order_id=order_id,
            workflow_id=workflow_id,
            supervisor_config_id=supervisor.id,
            order_context={"order_id": order_id, "customer_id": "stage3-test-customer"},
            current_order_state={"lifecycle": "open"},
            memory_summary=None,
            final_output=None,
        )
        identifiers = PersistedStage3Run(
            run_id=run.id,
            supervisor_id=supervisor.id,
            workflow_id=workflow_id,
        )

    try:
        yield identifiers
    finally:
        async with async_session_factory.begin() as session:
            await session.execute(delete(Run).where(Run.id == identifiers.run_id))
            await session.execute(
                delete(SupervisorConfig).where(SupervisorConfig.id == identifiers.supervisor_id)
            )


async def _load_run(run_id: UUID) -> Run:
    async with async_session_factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run


def _run_detail(run: Run) -> RunDetailResponse:
    run_data = RunResponse.model_validate(run).model_dump()
    return RunDetailResponse(
        **run_data,
        activities=[],
        workflow_state=None,
    )


@pytest.mark.asyncio
async def test_stage3_migration_adds_nullable_object_checked_final_output() -> None:
    async with engine.connect() as connection:
        columns = await connection.run_sync(lambda sync: inspect(sync).get_columns("runs"))
        constraints = await connection.run_sync(
            lambda sync: inspect(sync).get_check_constraints("runs")
        )

    final_output_column = next(column for column in columns if column["name"] == "final_output")
    final_output_constraint = next(
        constraint
        for constraint in constraints
        if constraint["name"] == "ck_runs_final_output_object"
    )
    normalized_sql = " ".join(final_output_constraint["sqltext"].lower().split())

    assert isinstance(final_output_column["type"], JSONB)
    assert final_output_column["nullable"] is True
    assert "final_output is null" in normalized_sql
    assert "jsonb_typeof(final_output) = 'object'" in normalized_sql


@pytest.mark.asyncio
async def test_final_output_constraint_rejects_non_object_json(
    stage3_run: PersistedStage3Run,
) -> None:
    async with async_session_factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    update(Run)
                    .where(Run.id == stage3_run.run_id)
                    .values(final_output=["not", "an", "object"])
                )

    persisted = await _load_run(stage3_run.run_id)
    assert persisted.final_output is None


@pytest.mark.asyncio
async def test_persistence_transition_writes_memory_and_final_output_for_api(
    stage3_run: PersistedStage3Run,
) -> None:
    recorded_at = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
    request = PersistenceTransitionRequest(
        run_id=str(stage3_run.run_id),
        workflow_id=stage3_run.workflow_id,
        temporal_run_id=f"temporal-{uuid4().hex}",
        activity_key=f"{stage3_run.workflow_id}:{stage3_run.run_id}:00000001",
        sequence=1,
        recorded_at=recorded_at.isoformat(),
        activity_type="final_output_generated",
        source="supervisor",
        status="recorded",
        summary="Generated and persisted the final supervisor report.",
        payload={"provider": "deterministic", "model": "deterministic-v1"},
        event_name=None,
        action_name=None,
        external_event_id=None,
        workflow_status=WorkflowStatus.COMPLETED,
        current_order_state={"lifecycle": "completed", "shipment": "delivered"},
        additional_instructions=[
            {
                "instruction_id": "stage3-instruction",
                "instruction": "Keep the final report concise.",
                "created_at": recorded_at.isoformat(),
            }
        ],
        next_wake_at=None,
        completion_reason="Order delivered under a Workflow-owned rule.",
        completed_at=recorded_at.isoformat(),
        memory_summary=COMPACT_MEMORY,
        final_output=FINAL_OUTPUT,
    )

    await persist_workflow_transition(request)
    persisted = await _load_run(stage3_run.run_id)

    assert persisted.status == WorkflowStatus.COMPLETED.value
    assert persisted.memory_summary == COMPACT_MEMORY
    assert persisted.final_output == FINAL_OUTPUT
    assert persisted.completion_reason == request.completion_reason

    run_response = RunResponse.model_validate(persisted).model_dump(mode="json")
    run_detail = _run_detail(persisted).model_dump(mode="json")
    required_final_fields = {
        "final_summary",
        "important_actions",
        "key_learnings",
        "recommendations",
    }

    assert set(run_response["final_output"]) == required_final_fields
    assert set(run_detail["final_output"]) == required_final_fields
    assert run_response["final_output"] == FINAL_OUTPUT
    assert run_detail["final_output"] == FINAL_OUTPUT
    assert run_response["memory_summary"] == COMPACT_MEMORY
    assert run_detail["memory_summary"] == COMPACT_MEMORY


@pytest.mark.asyncio
async def test_legacy_run_responses_expose_null_stage3_fields(
    stage3_run: PersistedStage3Run,
) -> None:
    persisted = await _load_run(stage3_run.run_id)

    run_response = RunResponse.model_validate(persisted).model_dump(mode="json")
    run_detail = _run_detail(persisted).model_dump(mode="json")

    assert run_response["memory_summary"] is None
    assert run_response["final_output"] is None
    assert run_detail["memory_summary"] is None
    assert run_detail["final_output"] is None
