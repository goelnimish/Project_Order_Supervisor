"""Focused HTTP coverage for the Stage 3.5 PostgreSQL analytics layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.routers.analytics as analytics_router
import app.routers.dependencies as router_dependencies
from app.config import get_settings
from app.database import get_session
from app.main import app
from app.models.activity import Activity
from app.models.run import ACTIVE_RUN_STATUSES, Run
from app.models.supervisor import SupervisorConfig
from app.repositories.activities import append_activity_idempotently
from app.repositories.runs import create_run, mark_run_completed
from app.repositories.supervisors import create_supervisor
from app.temporal.models import BUSINESS_ACTION_NAMES

BASE_TIME = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
TERMINAL_STATUSES = {"completed", "terminated", "failed"}


@dataclass(frozen=True)
class AnalyticsHarness:
    """HTTP client and its rollback-isolated PostgreSQL session."""

    client: AsyncClient
    session: AsyncSession


analytics_test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest_asyncio.fixture
async def analytics_harness() -> AsyncIterator[AnalyticsHarness]:
    """Present an empty database view and restore all pre-existing rows on rollback."""

    async with analytics_test_engine.connect() as connection:
        outer_transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        await session.execute(delete(Activity))
        await session.execute(delete(Run))
        await session.execute(delete(SupervisorConfig))
        await session.flush()

        async def override_session() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_session] = override_session
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield AnalyticsHarness(client=client, session=session)
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(router_dependencies.get_temporal_client, None)
            app.dependency_overrides.pop(router_dependencies.get_optional_temporal_client, None)
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()


async def _seed_supervisor(session: AsyncSession) -> SupervisorConfig:
    token = uuid4().hex
    return await create_supervisor(
        session,
        name=f"Analytics supervisor {token}",
        base_instruction="Keep the analytics test order moving safely.",
        available_actions=BUSINESS_ACTION_NAMES,
        default_wake_seconds=60,
    )


async def _seed_run(
    session: AsyncSession,
    supervisor: SupervisorConfig,
    *,
    status: str = "running",
    started_at: datetime = BASE_TIME,
    completed_at: datetime | None = None,
) -> Run:
    token = uuid4().hex
    initial_status = "running" if status in TERMINAL_STATUSES else status
    run = await create_run(
        session,
        order_id=f"analytics-order-{token}",
        workflow_id=f"order-supervisor:analytics-order-{token}",
        supervisor_config_id=supervisor.id,
        order_context={"order_id": f"analytics-order-{token}"},
        current_order_state={"lifecycle": "open"},
        status=initial_status,
        started_at=started_at,
    )
    if status in TERMINAL_STATUSES:
        finished = await mark_run_completed(
            session,
            run.id,
            status=status,
            completion_reason=f"Analytics test ended as {status}.",
            completed_at=completed_at or started_at + timedelta(minutes=5),
        )
        assert finished is run
    return run


def _source_for(activity_type: str) -> str:
    if activity_type in {"order_event_received", "duplicate_event_ignored"}:
        return "order_event"
    if activity_type.startswith("business_action"):
        return "business_action"
    if activity_type.startswith("supervisor_"):
        return "supervisor"
    if activity_type in {"scheduled_wake", "workflow_started"}:
        return "temporal"
    return "workflow"


async def _seed_activity(
    session: AsyncSession,
    run: Run,
    activity_type: str,
    *,
    created_at: datetime = BASE_TIME,
    status: str = "recorded",
    event_name: str | None = None,
    action_name: str | None = None,
    external_event_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Activity:
    activity, created = await append_activity_idempotently(
        session,
        run_id=run.id,
        activity_key=f"{run.workflow_id}:analytics:{uuid4().hex}",
        activity_type=activity_type,
        source=_source_for(activity_type),
        status=status,
        summary=f"Analytics fixture: {activity_type}.",
        payload=payload,
        event_name=event_name,
        action_name=action_name,
        external_event_id=external_event_id,
        created_at=created_at,
    )
    assert created is True
    return activity


async def _seed_important_event(
    session: AsyncSession,
    run: Run,
    *,
    event_id: str,
    event_name: str,
    received_at: datetime,
    wake_at: datetime,
) -> None:
    await _seed_activity(
        session,
        run,
        "order_event_received",
        created_at=received_at,
        event_name=event_name,
        external_event_id=event_id,
        payload={"event_id": event_id, "event_type": event_name},
    )
    await _seed_activity(
        session,
        run,
        "supervisor_wake_requested",
        created_at=wake_at,
        event_name=event_name,
        external_event_id=event_id,
        payload={"event_id": event_id, "event_type": event_name},
    )


async def _seed_executed_action(
    session: AsyncSession,
    run: Run,
    *,
    action_name: str,
    created_at: datetime,
    trigger: str = "event:shipment_delayed",
    status: str = "executed",
) -> None:
    await _seed_activity(
        session,
        run,
        "business_action_executed",
        created_at=created_at,
        status=status,
        action_name=action_name,
        payload={"trigger": trigger, "status": status},
    )


async def _summary(harness: AnalyticsHarness) -> dict[str, Any]:
    response = await harness.client.get("/api/analytics/summary")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_global_summary_for_empty_database(analytics_harness: AnalyticsHarness) -> None:
    assert await _summary(analytics_harness) == {
        "runs": {"total": 0, "active": 0, "completed": 0, "terminated": 0},
        "wake_suppression_rate": None,
        "business_actions_executed": 0,
        "average_time_to_first_intervention_seconds": None,
        "action_distribution": {name: 0 for name in BUSINESS_ACTION_NAMES},
    }


@pytest.mark.asyncio
async def test_global_summary_counts_run_outcomes(analytics_harness: AnalyticsHarness) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    for run_status in ACTIVE_RUN_STATUSES:
        await _seed_run(analytics_harness.session, supervisor, status=run_status)
    for run_status in ("completed", "terminated", "failed"):
        await _seed_run(analytics_harness.session, supervisor, status=run_status)

    summary = await _summary(analytics_harness)

    assert summary["runs"] == {
        "total": len(ACTIVE_RUN_STATUSES) + 3,
        "active": len(ACTIVE_RUN_STATUSES),
        "completed": 1,
        "terminated": 1,
    }


@pytest.mark.asyncio
async def test_business_action_total_counts_only_executed_rows(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    await _seed_executed_action(
        analytics_harness.session,
        run,
        action_name="message_customer",
        created_at=BASE_TIME,
    )
    await _seed_executed_action(
        analytics_harness.session,
        run,
        action_name="create_internal_note",
        created_at=BASE_TIME + timedelta(seconds=1),
    )
    await _seed_executed_action(
        analytics_harness.session,
        run,
        action_name="message_customer",
        created_at=BASE_TIME + timedelta(seconds=2),
        status="failed",
    )
    await _seed_activity(
        analytics_harness.session,
        run,
        "business_action_failed",
        status="failed",
        action_name="message_logistics_team",
    )

    assert (await _summary(analytics_harness))["business_actions_executed"] == 2


@pytest.mark.asyncio
async def test_action_distribution_returns_all_five_exact_names(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    expected = {name: index + 1 for index, name in enumerate(BUSINESS_ACTION_NAMES)}
    second = 0
    for action_name, count in expected.items():
        for _ in range(count):
            await _seed_executed_action(
                analytics_harness.session,
                run,
                action_name=action_name,
                created_at=BASE_TIME + timedelta(seconds=second),
            )
            second += 1

    summary = await _summary(analytics_harness)

    assert summary["action_distribution"] == expected
    assert summary["business_actions_executed"] == sum(expected.values())


@pytest.mark.asyncio
async def test_proposed_rejected_and_skipped_actions_are_not_executed(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    await _seed_activity(
        analytics_harness.session,
        run,
        "supervisor_decision",
        status="validated",
        payload={"proposed_action_names": ["message_customer"]},
    )
    await _seed_activity(
        analytics_harness.session,
        run,
        "business_action_rejected",
        status="rejected",
        action_name="message_customer",
    )
    await _seed_activity(
        analytics_harness.session,
        run,
        "business_action_skipped",
        status="rejected",
        action_name="create_internal_note",
    )

    summary = await _summary(analytics_harness)

    assert summary["business_actions_executed"] == 0
    assert summary["action_distribution"] == {name: 0 for name in BUSINESS_ACTION_NAMES}


@pytest.mark.asyncio
async def test_wake_suppression_rate_uses_only_immediate_event_decisions(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    for index in range(3):
        await _seed_activity(
            analytics_harness.session,
            run,
            "supervisor_wake_suppressed",
            created_at=BASE_TIME + timedelta(seconds=index),
        )
    for index in range(2):
        await _seed_activity(
            analytics_harness.session,
            run,
            "supervisor_wake_requested",
            created_at=BASE_TIME + timedelta(seconds=10 + index),
        )
    for unrelated_type in (
        "workflow_started",
        "scheduled_wake",
        "supervisor_invocation",
        "final_output_generated",
    ):
        await _seed_activity(analytics_harness.session, run, unrelated_type)

    rate = (await _summary(analytics_harness))["wake_suppression_rate"]

    assert rate == pytest.approx(3 / 5)


@pytest.mark.asyncio
async def test_wake_suppression_rate_is_null_for_zero_denominator(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    await _seed_activity(analytics_harness.session, run, "workflow_started")
    await _seed_activity(analytics_harness.session, run, "scheduled_wake")
    await _seed_activity(analytics_harness.session, run, "supervisor_invocation")

    assert (await _summary(analytics_harness))["wake_suppression_rate"] is None


@pytest.mark.asyncio
async def test_average_intervention_time_averages_only_eligible_runs(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    first = await _seed_run(analytics_harness.session, supervisor)
    await _seed_important_event(
        analytics_harness.session,
        first,
        event_id="older-same-type-event",
        event_name="shipment_delayed",
        received_at=BASE_TIME - timedelta(seconds=40),
        wake_at=BASE_TIME - timedelta(seconds=30),
    )
    await _seed_important_event(
        analytics_harness.session,
        first,
        event_id="important-first",
        event_name="shipment_delayed",
        received_at=BASE_TIME,
        wake_at=BASE_TIME + timedelta(seconds=5),
    )
    await _seed_executed_action(
        analytics_harness.session,
        first,
        action_name="message_logistics_team",
        created_at=BASE_TIME + timedelta(seconds=30),
    )

    second = await _seed_run(analytics_harness.session, supervisor)
    await _seed_important_event(
        analytics_harness.session,
        second,
        event_id="important-second",
        event_name="payment_failed",
        received_at=BASE_TIME + timedelta(minutes=1),
        wake_at=BASE_TIME + timedelta(minutes=1, seconds=10),
    )
    await _seed_executed_action(
        analytics_harness.session,
        second,
        action_name="message_payments_team",
        created_at=BASE_TIME + timedelta(minutes=2, seconds=5),
        trigger="event:payment_failed",
    )

    ineligible = await _seed_run(analytics_harness.session, supervisor)
    await _seed_important_event(
        analytics_harness.session,
        ineligible,
        event_id="important-no-action",
        event_name="shipment_delayed",
        received_at=BASE_TIME,
        wake_at=BASE_TIME + timedelta(seconds=1),
    )
    await _seed_activity(
        analytics_harness.session,
        ineligible,
        "business_action_rejected",
        created_at=BASE_TIME + timedelta(seconds=50),
        status="rejected",
        action_name="message_logistics_team",
    )

    average = (await _summary(analytics_harness))["average_time_to_first_intervention_seconds"]

    assert average == pytest.approx((30 + 65) / 2)


@pytest.mark.asyncio
async def test_average_intervention_time_is_null_without_eligible_run(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    await _seed_important_event(
        analytics_harness.session,
        run,
        event_id="important-scheduled-only",
        event_name="shipment_delayed",
        received_at=BASE_TIME,
        wake_at=BASE_TIME + timedelta(seconds=2),
    )
    await _seed_executed_action(
        analytics_harness.session,
        run,
        action_name="create_internal_note",
        created_at=BASE_TIME + timedelta(seconds=20),
        trigger="scheduled_wake",
    )

    summary = await _summary(analytics_harness)

    assert summary["average_time_to_first_intervention_seconds"] is None


@pytest.mark.asyncio
async def test_existing_run_analytics_returns_duration_and_activity_counts(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(
        analytics_harness.session,
        supervisor,
        status="completed",
        started_at=BASE_TIME,
        completed_at=BASE_TIME + timedelta(minutes=5),
    )
    for index in range(2):
        await _seed_activity(
            analytics_harness.session,
            run,
            "order_event_received",
            created_at=BASE_TIME + timedelta(seconds=index),
        )
    for index in range(3):
        await _seed_activity(
            analytics_harness.session,
            run,
            "supervisor_invocation",
            created_at=BASE_TIME + timedelta(seconds=10 + index),
        )
    await _seed_activity(analytics_harness.session, run, "supervisor_wake_suppressed")
    for index in range(2):
        await _seed_executed_action(
            analytics_harness.session,
            run,
            action_name="create_internal_note",
            created_at=BASE_TIME + timedelta(seconds=20 + index),
        )

    response = await analytics_harness.client.get(f"/api/runs/{run.id}/analytics")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "run_id": str(run.id),
        "order_id": run.order_id,
        "duration_seconds": 300.0,
        "events_received": 2,
        "supervisor_invocations": 3,
        "wake_suppressions": 1,
        "business_actions_executed": 2,
    }

    active_started_at = datetime.now(UTC) - timedelta(seconds=120)
    active = await _seed_run(
        analytics_harness.session,
        supervisor,
        status="sleeping",
        started_at=active_started_at,
    )
    active_response = await analytics_harness.client.get(f"/api/runs/{active.id}/analytics")
    assert active_response.status_code == 200
    assert 118 <= active_response.json()["duration_seconds"] <= 122


@pytest.mark.asyncio
async def test_duplicate_suppression_does_not_increase_event_count(
    analytics_harness: AnalyticsHarness,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    await _seed_activity(
        analytics_harness.session,
        run,
        "order_event_received",
        external_event_id="unique-event",
    )
    for index in range(4):
        await _seed_activity(
            analytics_harness.session,
            run,
            "duplicate_event_ignored",
            created_at=BASE_TIME + timedelta(seconds=index + 1),
            external_event_id="unique-event",
        )

    response = await analytics_harness.client.get(f"/api/runs/{run.id}/analytics")

    assert response.status_code == 200
    assert response.json()["events_received"] == 1


@pytest.mark.asyncio
async def test_unknown_run_analytics_returns_sanitized_404(
    analytics_harness: AnalyticsHarness,
) -> None:
    response = await analytics_harness.client.get(f"/api/runs/{uuid4()}/analytics")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "run_not_found",
            "message": "Supervisor run was not found.",
        }
    }


@pytest.mark.asyncio
async def test_analytics_are_temporal_independent_and_read_only(
    analytics_harness: AnalyticsHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = await _seed_supervisor(analytics_harness.session)
    run = await _seed_run(analytics_harness.session, supervisor)
    await _seed_activity(analytics_harness.session, run, "order_event_received")

    temporal_connection_attempts = 0

    async def fail_if_temporal_is_contacted(*_args: object, **_kwargs: object) -> None:
        nonlocal temporal_connection_attempts
        temporal_connection_attempts += 1
        raise AssertionError("Analytics must not connect to Temporal.")

    monkeypatch.setattr(router_dependencies, "connect_temporal", fail_if_temporal_is_contacted)

    async def database_snapshot() -> tuple[int, int, int, str, datetime]:
        supervisor_count = await analytics_harness.session.scalar(
            select(func.count()).select_from(SupervisorConfig)
        )
        run_count = await analytics_harness.session.scalar(select(func.count()).select_from(Run))
        activity_count = await analytics_harness.session.scalar(
            select(func.count()).select_from(Activity)
        )
        run_state = (
            await analytics_harness.session.execute(
                select(Run.status, Run.updated_at).where(Run.id == run.id)
            )
        ).one()
        return (
            int(supervisor_count or 0),
            int(run_count or 0),
            int(activity_count or 0),
            run_state.status,
            run_state.updated_at,
        )

    before = await database_snapshot()
    summary_response = await analytics_harness.client.get("/api/analytics/summary")
    run_response = await analytics_harness.client.get(f"/api/runs/{run.id}/analytics")
    after = await database_snapshot()

    assert summary_response.status_code == 200
    assert run_response.status_code == 200
    assert temporal_connection_attempts == 0
    assert after == before
    assert not analytics_harness.session.new
    assert not analytics_harness.session.dirty
    assert not analytics_harness.session.deleted


@pytest.mark.asyncio
async def test_database_unavailable_errors_are_sanitized(
    analytics_harness: AnalyticsHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "postgresql://user:SENSITIVE_PASSWORD@private-host/order_supervisor"

    async def fail_global(_session: AsyncSession):
        raise SQLAlchemyError(secret)

    async def fail_run(_session: AsyncSession, _run_id: UUID):
        raise SQLAlchemyError(secret)

    monkeypatch.setattr(analytics_router, "load_global_analytics", fail_global)
    monkeypatch.setattr(analytics_router, "load_run_analytics", fail_run)

    summary_response = await analytics_harness.client.get("/api/analytics/summary")
    run_response = await analytics_harness.client.get(f"/api/runs/{uuid4()}/analytics")
    expected = {
        "detail": {
            "code": "database_unavailable",
            "message": "PostgreSQL is currently unavailable.",
        }
    }

    assert summary_response.status_code == 503
    assert run_response.status_code == 503
    assert summary_response.json() == expected
    assert run_response.json() == expected
    assert "SENSITIVE_PASSWORD" not in summary_response.text
    assert "SENSITIVE_PASSWORD" not in run_response.text
