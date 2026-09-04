"""Deterministic cross-stack acceptance flows for the original assignment."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.database import async_session_factory, engine
from app.main import app
from app.models.run import Run
from app.models.supervisor import SupervisorConfig
from app.routers.dependencies import get_optional_temporal_client, get_temporal_client
from app.temporal.activities import fake_supervisor, generate_final_output
from app.temporal.business_actions import execute_business_action
from app.temporal.models import (
    BUSINESS_ACTION_NAMES,
    TerminateRequest,
    WorkflowSnapshot,
    WorkflowStatus,
)
from app.temporal.persistence_activities import persist_workflow_transition
from app.temporal.workflows import OrderSupervisorWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_TASK_QUEUE = "order-supervisor-stage45-acceptance"
WAKE_SECONDS = 30
WAIT_TIMEOUT_SECONDS = 10.0
INSTRUCTION = "For this order, prioritize speed over cost."


class AcceptanceTemporalClient:
    """Route API starts to the isolated time-skipping acceptance Worker."""

    def __init__(self, environment: WorkflowEnvironment) -> None:
        self.environment = environment

    async def start_workflow(
        self,
        workflow: object,
        workflow_input: object,
        *,
        id: str,
        task_queue: str,
    ):
        del task_queue
        return await self.environment.client.start_workflow(
            workflow,
            workflow_input,
            id=id,
            task_queue=TEST_TASK_QUEUE,
        )

    def get_workflow_handle(
        self,
        workflow_id: str,
        *,
        first_execution_run_id: str | None = None,
    ):
        return self.environment.client.get_workflow_handle(
            workflow_id,
            first_execution_run_id=first_execution_run_id,
        )


@dataclass
class AcceptanceHarness:
    client: AsyncClient
    environment: WorkflowEnvironment
    temporal: AcceptanceTemporalClient
    run_ids: list[UUID] = field(default_factory=list)
    supervisor_ids: list[UUID] = field(default_factory=list)
    handles: list[Any] = field(default_factory=list)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_environment() -> AsyncIterator[WorkflowEnvironment]:
    """Use production Workflow/Activities with an isolated, time-skipping server."""

    await engine.dispose()
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[
                fake_supervisor,
                generate_final_output,
                execute_business_action,
                persist_workflow_transition,
            ],
        ):
            yield environment
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def acceptance_harness(
    temporal_environment: WorkflowEnvironment,
) -> AsyncIterator[AcceptanceHarness]:
    temporal = AcceptanceTemporalClient(temporal_environment)

    async def override_temporal_client() -> AcceptanceTemporalClient:
        return temporal

    app.dependency_overrides[get_temporal_client] = override_temporal_client
    app.dependency_overrides[get_optional_temporal_client] = override_temporal_client
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        harness = AcceptanceHarness(
            client=client,
            environment=temporal_environment,
            temporal=temporal,
        )
        try:
            yield harness
        finally:
            for handle in harness.handles:
                try:
                    snapshot = await handle.query(OrderSupervisorWorkflow.get_state)
                    if not snapshot.completed:
                        await handle.signal(
                            OrderSupervisorWorkflow.request_termination,
                            TerminateRequest(reason="Stage 4.5 acceptance cleanup"),
                        )
                        with temporal_environment.auto_time_skipping_disabled():
                            await asyncio.wait_for(
                                handle.result(),
                                timeout=WAIT_TIMEOUT_SECONDS,
                            )
                except Exception:
                    # A completed Workflow cannot accept cleanup Signals and needs no action.
                    pass

            async with async_session_factory.begin() as session:
                for run_id in harness.run_ids:
                    await session.execute(delete(Run).where(Run.id == run_id))
                for supervisor_id in harness.supervisor_ids:
                    await session.execute(
                        delete(SupervisorConfig).where(SupervisorConfig.id == supervisor_id)
                    )

    app.dependency_overrides.pop(get_temporal_client, None)
    app.dependency_overrides.pop(get_optional_temporal_client, None)
    await engine.dispose()


async def _create_supervisor(harness: AcceptanceHarness) -> dict[str, Any]:
    token = uuid4().hex
    response = await harness.client.post(
        "/api/supervisors",
        json={
            "name": f"Stage 4.5 acceptance supervisor {token}",
            "base_instruction": "Keep the order moving and record concise evidence.",
            "available_actions": list(BUSINESS_ACTION_NAMES),
            "default_wake_seconds": WAKE_SECONDS,
            "wake_aggressiveness": "moderate",
            "model_config": {
                "provider": "deterministic",
                "model": None,
                "temperature": 0,
            },
        },
    )
    assert response.status_code == 201, response.text
    supervisor = response.json()
    harness.supervisor_ids.append(UUID(supervisor["id"]))
    return supervisor


async def _start_run(
    harness: AcceptanceHarness,
    supervisor_id: str,
    *,
    order_id: str,
) -> tuple[dict[str, Any], Any]:
    response = await harness.client.post(
        "/api/runs",
        json={
            "order_id": order_id,
            "supervisor_config_id": supervisor_id,
            "order_context": {
                "customer_id": f"customer-{order_id}",
                "initial_state": {"priority": "standard"},
            },
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    harness.run_ids.append(UUID(run["id"]))
    handle = harness.temporal.get_workflow_handle(
        run["workflow_id"],
        first_execution_run_id=run["temporal_run_id"],
    )
    harness.handles.append(handle)
    return run, handle


async def _wait_for_snapshot(
    handle: Any,
    predicate: Callable[[WorkflowSnapshot], bool],
) -> WorkflowSnapshot:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    last_snapshot: WorkflowSnapshot | None = None
    while loop.time() < deadline:
        last_snapshot = await handle.query(OrderSupervisorWorkflow.get_state)
        if predicate(last_snapshot):
            return last_snapshot
        await asyncio.sleep(0.02)
    pytest.fail(f"Workflow state did not converge; last snapshot: {last_snapshot!r}")


async def _wait_for_run(
    harness: AcceptanceHarness,
    run_id: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    last_run: dict[str, Any] | None = None
    while loop.time() < deadline:
        response = await harness.client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        last_run = response.json()
        if predicate(last_run):
            return last_run
        await asyncio.sleep(0.02)
    pytest.fail(f"Persisted run did not converge; last response: {last_run!r}")


async def _send_event(
    harness: AcceptanceHarness,
    run_id: str,
    *,
    event_id: str,
    event_type: str,
) -> None:
    response = await harness.client.post(
        f"/api/runs/{run_id}/events",
        json={
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": "2026-09-04T12:00:00Z",
            "payload": {"source": "stage45-acceptance"},
        },
    )
    assert response.status_code == 202, response.text


def _activity_types(run: dict[str, Any]) -> list[str]:
    return [activity["activity_type"] for activity in run["activities"]]


async def test_original_assignment_happy_path(
    acceptance_harness: AcceptanceHarness,
) -> None:
    """Exercise the mandatory assignment journey through API, Temporal, and PostgreSQL."""

    harness = acceptance_harness
    token = uuid4().hex
    supervisor = await _create_supervisor(harness)
    run, handle = await _start_run(
        harness,
        supervisor["id"],
        order_id=f"stage45-happy-path-{token}",
    )

    initial = await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
    )
    assert initial.next_wake_at is not None
    assert initial.memory_summary is not None

    await _send_event(
        harness,
        run["id"],
        event_id=f"payment-{token}",
        event_type="payment_confirmed",
    )
    routine = await _wait_for_snapshot(
        handle,
        lambda state: state.order_state.payment == "confirmed" and state.pending_event_count == 0,
    )
    assert routine.supervisor_invocation_count == initial.supervisor_invocation_count
    await _wait_for_run(
        harness,
        run["id"],
        lambda item: "supervisor_wake_suppressed" in _activity_types(item),
    )

    await _send_event(
        harness,
        run["id"],
        event_id=f"delay-{token}",
        event_type="shipment_delayed",
    )
    important = await _wait_for_snapshot(
        handle,
        lambda state: (
            state.supervisor_invocation_count == 2
            and state.is_sleeping
            and state.memory_summary is not None
            and "message_logistics_team" in state.memory_summary.actions_taken
        ),
    )
    assert important.order_state.shipment == "delayed"
    after_action = await _wait_for_run(
        harness,
        run["id"],
        lambda item: any(
            activity["activity_type"] == "business_action_executed"
            and activity["action_name"] == "message_logistics_team"
            for activity in item["activities"]
        ),
    )
    action_rows = [
        activity
        for activity in after_action["activities"]
        if activity["activity_type"] == "business_action_executed"
    ]
    assert len(action_rows) == 1
    assert action_rows[0]["payload"]["simulation"]["delivery"] == "simulated"

    instruction_response = await harness.client.post(
        f"/api/runs/{run['id']}/instructions",
        json={"instruction": INSTRUCTION},
    )
    assert instruction_response.status_code == 202, instruction_response.text
    instructed = await _wait_for_snapshot(
        handle,
        lambda state: any(
            item.instruction == INSTRUCTION for item in state.additional_instructions
        ),
    )
    instruction_id = next(
        item.instruction_id
        for item in instructed.additional_instructions
        if item.instruction == INSTRUCTION
    )

    await harness.environment.sleep(timedelta(seconds=WAKE_SECONDS + 1))
    scheduled = await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count >= 3 and state.is_sleeping,
    )
    assert scheduled.supervisor_invocation_count == 3
    after_scheduled = await _wait_for_run(
        harness,
        run["id"],
        lambda item: any(
            activity["activity_type"] == "supervisor_invocation"
            and activity["payload"].get("trigger") == "scheduled_wake"
            and instruction_id in activity["payload"].get("context_instruction_ids", [])
            for activity in item["activities"]
        ),
    )
    assert any(
        item["instruction"] == INSTRUCTION for item in after_scheduled["additional_instructions"]
    )

    await _send_event(
        harness,
        run["id"],
        event_id=f"delivered-{token}",
        event_type="delivered",
    )
    with harness.environment.auto_time_skipping_disabled():
        result = await asyncio.wait_for(handle.result(), timeout=WAIT_TIMEOUT_SECONDS)
    assert result["workflow_status"] == WorkflowStatus.COMPLETED.value

    completed = await _wait_for_run(
        harness,
        run["id"],
        lambda item: item["status"] == "completed" and item["final_output"] is not None,
    )
    activity_types = set(_activity_types(completed))
    assert {
        "order_event_received",
        "supervisor_wake_suppressed",
        "supervisor_wake_requested",
        "supervisor_invocation",
        "supervisor_decision",
        "business_action_executed",
        "memory_updated",
        "instruction_added",
        "scheduled_wake",
        "workflow_completion_authorized",
        "final_output_generated",
        "workflow_completed",
    }.issubset(activity_types)
    assert completed["current_order_state"]["lifecycle"] == "completed"
    assert completed["current_order_state"]["shipment"] == "delivered"
    assert completed["memory_summary"] is not None
    assert "message_logistics_team" in completed["memory_summary"]["actions_taken"]
    assert any(item["instruction"] == INSTRUCTION for item in completed["additional_instructions"])
    assert set(completed["final_output"]) == {
        "final_summary",
        "important_actions",
        "key_learnings",
        "recommendations",
    }
    assert all(completed["final_output"][field] for field in completed["final_output"])
    assert (
        sum(
            activity["action_name"] == "message_logistics_team"
            for activity in completed["activities"]
            if activity["activity_type"] == "business_action_executed"
        )
        == 1
    )


async def test_graceful_termination_persists_reason_and_final_state(
    acceptance_harness: AcceptanceHarness,
) -> None:
    """AT-39: the normal terminate API persists a graceful Workflow-owned outcome."""

    harness = acceptance_harness
    token = uuid4().hex
    reason = "Operator completed the Stage 4.5 termination check."
    supervisor = await _create_supervisor(harness)
    run, handle = await _start_run(
        harness,
        supervisor["id"],
        order_id=f"stage45-terminate-{token}",
    )
    await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
    )

    response = await harness.client.post(
        f"/api/runs/{run['id']}/terminate",
        json={"reason": reason},
    )
    assert response.status_code == 202, response.text
    assert response.json()["signal"] == "request_termination"
    with harness.environment.auto_time_skipping_disabled():
        result = await asyncio.wait_for(handle.result(), timeout=WAIT_TIMEOUT_SECONDS)
    assert result["workflow_status"] == WorkflowStatus.TERMINATED.value

    terminated = await _wait_for_run(
        harness,
        run["id"],
        lambda item: item["status"] == "terminated" and item["final_output"] is not None,
    )
    assert terminated["completion_reason"] == f"Terminated by operator: {reason}"
    assert terminated["next_wake_at"] is None
    assert {
        "termination_requested",
        "workflow_termination_authorized",
        "final_output_generated",
        "workflow_terminated",
    }.issubset(set(_activity_types(terminated)))
