"""PostgreSQL integration tests for the Stage 2 persistence layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models.supervisor import ALLOWED_ACTION_NAMES
from app.repositories.activities import (
    append_activity_idempotently,
    list_activities_for_run,
)
from app.repositories.runs import (
    create_run,
    get_active_run_for_order,
    get_run,
    list_runs,
    mark_run_completed,
    update_run_state,
)
from app.repositories.supervisors import create_supervisor, get_supervisor, list_supervisors

test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
repository_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    """Use one rollback-only transaction without deleting any shared records."""

    async with repository_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def _create_test_supervisor(session: AsyncSession):
    token = uuid4().hex
    return await create_supervisor(
        session,
        name=f"Repository supervisor {token}",
        base_instruction="Keep this isolated repository test order moving safely.",
        available_actions=ALLOWED_ACTION_NAMES,
        default_wake_seconds=60,
        wake_aggressiveness="moderate",
        model_configuration={"provider": "none"},
    )


async def _create_test_run(session: AsyncSession):
    supervisor = await _create_test_supervisor(session)
    order_id = f"repository-order-{uuid4().hex}"
    run = await create_run(
        session,
        order_id=order_id,
        workflow_id=f"order-supervisor:{order_id}",
        supervisor_config_id=supervisor.id,
        order_context={"order_id": order_id, "customer_id": "test-customer"},
        current_order_state={"lifecycle": "open"},
    )
    return supervisor, run


@pytest.mark.asyncio
async def test_stage2_migration_created_expected_schema() -> None:
    async with test_engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        run_indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes("runs"))
        activity_constraints = await connection.run_sync(
            lambda sync: inspect(sync).get_unique_constraints("activities")
        )

    assert {"supervisor_configs", "runs", "activities"}.issubset(tables)
    active_index = next(
        index for index in run_indexes if index["name"] == "uq_runs_active_order_id"
    )
    assert active_index["unique"] is True
    assert any(
        constraint["name"] == "uq_activities_activity_key" for constraint in activity_constraints
    )


@pytest.mark.asyncio
async def test_supervisor_create_read_and_list(database_session: AsyncSession) -> None:
    supervisor = await _create_test_supervisor(database_session)

    loaded = await get_supervisor(database_session, supervisor.id)
    supervisors = await list_supervisors(database_session)

    assert loaded is supervisor
    assert loaded.available_actions == list(ALLOWED_ACTION_NAMES)
    assert loaded.model_configuration == {"provider": "none"}
    assert supervisor.id in {item.id for item in supervisors}


@pytest.mark.asyncio
async def test_run_create_update_list_and_complete(database_session: AsyncSession) -> None:
    _, run = await _create_test_run(database_session)
    wake_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    active = await get_active_run_for_order(database_session, run.order_id)
    updated = await update_run_state(
        database_session,
        run.id,
        status="sleeping",
        temporal_run_id=str(uuid4()),
        current_order_state={"lifecycle": "open", "shipment": "created"},
        additional_instructions=[{"instruction": "Prioritize speed."}],
        next_wake_at=wake_at,
    )
    sleeping_runs = await list_runs(database_session, status="sleeping")

    assert active is run
    assert updated is run
    assert run.id in {item.id for item in sleeping_runs}
    assert run.additional_instructions == [{"instruction": "Prioritize speed."}]
    assert run.next_wake_at == wake_at

    completed = await mark_run_completed(
        database_session,
        run.id,
        status="completed",
        completion_reason="Order delivered in repository test.",
        completed_at=datetime(2026, 9, 4, 12, 1, tzinfo=UTC),
    )

    reloaded = await get_run(database_session, run.id)
    assert completed is run
    assert reloaded is run
    assert reloaded.status == "completed"
    assert await get_active_run_for_order(database_session, run.order_id) is None

    replacement = await create_run(
        database_session,
        order_id=run.order_id,
        workflow_id=run.workflow_id,
        supervisor_config_id=run.supervisor_config_id,
        order_context=run.order_context,
    )
    assert replacement.id != run.id


@pytest.mark.asyncio
async def test_partial_unique_index_rejects_two_active_runs(
    database_session: AsyncSession,
) -> None:
    _, run = await _create_test_run(database_session)

    with pytest.raises(IntegrityError):
        async with database_session.begin_nested():
            await create_run(
                database_session,
                order_id=run.order_id,
                workflow_id=run.workflow_id,
                supervisor_config_id=run.supervisor_config_id,
                order_context=run.order_context,
            )

    assert await get_active_run_for_order(database_session, run.order_id) is run


@pytest.mark.asyncio
async def test_activity_append_is_idempotent(database_session: AsyncSession) -> None:
    _, run = await _create_test_run(database_session)
    activity_key = f"{run.workflow_id}:1"
    created_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    first, first_created = await append_activity_idempotently(
        database_session,
        run_id=run.id,
        activity_key=activity_key,
        activity_type="order_event_received",
        source="workflow",
        event_name="payment_confirmed",
        status="recorded",
        summary="Payment confirmation was received.",
        payload={"event_type": "payment_confirmed"},
        external_event_id="repository-event-1",
        created_at=created_at,
    )
    second, second_created = await append_activity_idempotently(
        database_session,
        run_id=run.id,
        activity_key=activity_key,
        activity_type="order_event_received",
        source="workflow",
        event_name="payment_confirmed",
        status="recorded",
        summary="Payment confirmation was received.",
        payload={"event_type": "payment_confirmed"},
        external_event_id="repository-event-1",
        created_at=created_at,
    )
    activities = await list_activities_for_run(database_session, run.id)

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert [item.activity_key for item in activities] == [activity_key]
    assert activities[0].external_event_id == "repository-event-1"
