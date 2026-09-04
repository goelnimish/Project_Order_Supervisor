"""Integration coverage for strict, retry-safe simulated business actions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from temporalio.exceptions import ApplicationError

from app.database import async_session_factory, engine
from app.models.activity import Activity
from app.models.run import Run
from app.models.supervisor import SupervisorConfig
from app.repositories.runs import create_run
from app.repositories.supervisors import create_supervisor
from app.temporal.business_actions import execute_business_action
from app.temporal.models import BUSINESS_ACTION_NAMES, BusinessActionExecutionRequest

ACTION_DESTINATIONS = (
    ("message_fulfillment_team", "fulfillment_team"),
    ("message_payments_team", "payments_team"),
    ("message_logistics_team", "logistics_team"),
    ("message_customer", "customer"),
    ("create_internal_note", "internal_order_record"),
)

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def isolate_shared_database_pool() -> AsyncIterator[None]:
    """Keep the production Activity's shared engine on one test event loop."""

    try:
        yield
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class BusinessActionRun:
    """Identifiers for one committed run exercised through the real Activity."""

    run_id: UUID
    supervisor_id: UUID
    workflow_id: str


@pytest_asyncio.fixture(loop_scope="module")
async def business_action_run() -> AsyncIterator[BusinessActionRun]:
    """Create committed records because the Activity uses its own database session."""

    token = uuid4().hex
    order_id = f"business-action-{token}"
    workflow_id = f"order-supervisor:{order_id}"

    async with async_session_factory.begin() as session:
        supervisor = await create_supervisor(
            session,
            name=f"Business action supervisor {token}",
            base_instruction="Exercise strict simulated business actions.",
            available_actions=BUSINESS_ACTION_NAMES,
            default_wake_seconds=60,
        )
        run = await create_run(
            session,
            order_id=order_id,
            workflow_id=workflow_id,
            supervisor_config_id=supervisor.id,
            order_context={"order_id": order_id},
        )
        identifiers = BusinessActionRun(
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


def _request(
    action_run: BusinessActionRun,
    *,
    action_name: str,
    action_index: int = 0,
    arguments: dict[str, Any] | None = None,
    available_actions: list[str] | None = None,
    idempotency_key: str | None = None,
) -> BusinessActionExecutionRequest:
    return BusinessActionExecutionRequest(
        run_id=str(action_run.run_id),
        workflow_id=action_run.workflow_id,
        idempotency_key=idempotency_key
        or f"{action_run.workflow_id}:{action_run.run_id}:supervisor:1:action:{action_index}",
        requested_at=datetime(2026, 9, 4, 12, action_index, tzinfo=UTC).isoformat(),
        trigger="event:shipment_delayed",
        supervisor_invocation=1,
        action_index=action_index,
        action_name=action_name,
        arguments=arguments
        if arguments is not None
        else {"content": f"Take the safe simulated action {action_name}."},
        available_actions=available_actions
        if available_actions is not None
        else list(BUSINESS_ACTION_NAMES),
    )


async def _persisted_actions(run_id: UUID) -> list[Activity]:
    async with async_session_factory() as session:
        rows = await session.scalars(
            select(Activity)
            .where(
                Activity.run_id == run_id,
                Activity.activity_type == "business_action_executed",
            )
            .order_by(Activity.created_at, Activity.activity_key)
        )
        return list(rows)


@pytest.mark.parametrize(("action_name", "destination"), ACTION_DESTINATIONS)
async def test_each_exact_business_action_executes_and_persists(
    business_action_run: BusinessActionRun,
    action_name: str,
    destination: str,
) -> None:
    """Every required action reaches its explicit handler and audit row."""

    assert tuple(name for name, _destination in ACTION_DESTINATIONS) == BUSINESS_ACTION_NAMES
    request = _request(business_action_run, action_name=action_name)

    result = await execute_business_action(request)
    persisted = await _persisted_actions(business_action_run.run_id)

    assert result.action_name == action_name
    assert result.idempotency_key == request.idempotency_key
    assert result.status == "executed"
    assert result.persisted is True
    assert result.details == {
        "new_record": True,
        "destination": destination,
        "delivery": "simulated",
    }
    assert len(persisted) == 1
    assert persisted[0].activity_key == request.idempotency_key
    assert persisted[0].activity_type == "business_action_executed"
    assert persisted[0].source == "business_action"
    assert persisted[0].action_name == action_name
    assert persisted[0].status == "executed"
    assert persisted[0].payload["arguments"] == request.arguments
    assert persisted[0].payload["simulation"] == {
        "destination": destination,
        "delivery": "simulated",
    }


async def test_duplicate_business_action_retry_persists_only_one_row(
    business_action_run: BusinessActionRun,
) -> None:
    request = _request(
        business_action_run,
        action_name="message_logistics_team",
    )

    first = await execute_business_action(request)
    retried = await execute_business_action(request)
    persisted = await _persisted_actions(business_action_run.run_id)

    assert first.details["new_record"] is True
    assert retried.details["new_record"] is False
    assert first.idempotency_key == retried.idempotency_key
    assert len(persisted) == 1
    assert persisted[0].activity_key == request.idempotency_key


async def test_disabled_business_action_does_not_execute(
    business_action_run: BusinessActionRun,
) -> None:
    request = _request(
        business_action_run,
        action_name="message_customer",
        available_actions=["create_internal_note"],
    )

    with pytest.raises(ApplicationError) as raised:
        await execute_business_action(request)

    assert raised.value.type == "DisabledBusinessAction"
    assert raised.value.non_retryable is True
    assert await _persisted_actions(business_action_run.run_id) == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"content": "   "},
        {"content": "Contact logistics.", "unexpected": "not allowed"},
        {"content": "Terminate workflow immediately."},
    ],
)
async def test_malformed_business_action_arguments_do_not_execute(
    business_action_run: BusinessActionRun,
    arguments: dict[str, Any],
) -> None:
    request = _request(
        business_action_run,
        action_name="message_logistics_team",
        arguments=arguments,
    )

    with pytest.raises(ApplicationError) as raised:
        await execute_business_action(request)

    assert raised.value.type == "InvalidBusinessAction"
    assert raised.value.non_retryable is True
    assert await _persisted_actions(business_action_run.run_id) == []


async def test_unknown_business_action_name_does_not_execute(
    business_action_run: BusinessActionRun,
) -> None:
    request = _request(
        business_action_run,
        action_name="send_sms",
        available_actions=["send_sms"],
    )

    with pytest.raises(ApplicationError) as raised:
        await execute_business_action(request)

    assert raised.value.type == "InvalidBusinessAction"
    assert raised.value.non_retryable is True
    assert await _persisted_actions(business_action_run.run_id) == []


async def test_semantic_idempotency_collision_fails_and_preserves_original_evidence(
    business_action_run: BusinessActionRun,
) -> None:
    original = _request(
        business_action_run,
        action_name="create_internal_note",
        arguments={"content": "Record the first verified operational fact."},
    )
    conflicting = replace(
        original,
        arguments={"content": "Replace the note with different evidence."},
    )

    await execute_business_action(original)
    with pytest.raises(ApplicationError) as raised:
        await execute_business_action(conflicting)

    persisted = await _persisted_actions(business_action_run.run_id)
    assert raised.value.type == "BusinessActionIdempotencyConflict"
    assert raised.value.non_retryable is True
    assert len(persisted) == 1
    assert persisted[0].activity_key == original.idempotency_key
    assert persisted[0].action_name == original.action_name
    assert persisted[0].payload["arguments"] == original.arguments
