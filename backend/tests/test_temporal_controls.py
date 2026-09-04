"""Focused time-skipping tests for Stage 2 Workflow controls and persistence."""

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.temporal.activities import (
    FAKE_SUPERVISOR_ACTIVITY_NAME,
    fake_supervisor,
    generate_final_output,
)
from app.temporal.models import (
    BusinessActionExecutionRequest,
    BusinessActionExecutionResult,
    EventType,
    FakeSupervisorDecision,
    FakeSupervisorRequest,
    InterruptRequest,
    OrderContext,
    OrderEvent,
    PauseRequest,
    PersistenceTransitionRequest,
    ResumeRequest,
    RunInstruction,
    TerminateRequest,
    WorkflowInput,
    WorkflowSnapshot,
    WorkflowStatus,
    workflow_id_for_order,
)
from app.temporal.workflows import (
    EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME,
    PERSIST_TRANSITION_ACTIVITY_NAME,
    OrderSupervisorWorkflow,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_TASK_QUEUE = "order-supervisor-stage2-control-tests"
QUERY_TIMEOUT_SECONDS = 5.0
DEFAULT_TEST_RUN_ID = "00000000-0000-0000-0000-000000000001"
SUPERVISOR_REQUESTS: list[FakeSupervisorRequest] = []
PERSISTED_TRANSITIONS: list[PersistenceTransitionRequest] = []
PERSISTENCE_ATTEMPTS: dict[str, int] = {}
PERSISTENCE_FAILURES_REMAINING: dict[str, int] = {}

SnapshotPredicate = Callable[[WorkflowSnapshot], bool]
WorkflowHandleType = WorkflowHandle[OrderSupervisorWorkflow, WorkflowSnapshot]


@activity.defn(name=FAKE_SUPERVISOR_ACTIVITY_NAME)
async def recording_fake_supervisor(request: FakeSupervisorRequest) -> FakeSupervisorDecision:
    SUPERVISOR_REQUESTS.append(request)
    return await fake_supervisor(request)


@activity.defn(name=PERSIST_TRANSITION_ACTIVITY_NAME)
async def record_persistence_transition(request: PersistenceTransitionRequest) -> None:
    PERSISTENCE_ATTEMPTS[request.activity_key] = (
        PERSISTENCE_ATTEMPTS.get(request.activity_key, 0) + 1
    )
    failures_remaining = PERSISTENCE_FAILURES_REMAINING.get(request.activity_key, 0)
    if failures_remaining:
        PERSISTENCE_FAILURES_REMAINING[request.activity_key] = failures_remaining - 1
        raise RuntimeError("Intentional retryable persistence failure")
    PERSISTED_TRANSITIONS.append(request)


@activity.defn(name=EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME)
async def record_business_action(
    request: BusinessActionExecutionRequest,
) -> BusinessActionExecutionResult:
    return BusinessActionExecutionResult(
        action_name=request.action_name,
        idempotency_key=request.idempotency_key,
        status="executed",
        summary=f"Simulated {request.action_name} in the control test.",
        persisted=True,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_environment() -> WorkflowEnvironment:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[
                recording_fake_supervisor,
                generate_final_output,
                record_business_action,
                record_persistence_transition,
            ],
        ):
            yield environment


def _workflow_input(
    order_id: str,
    *,
    wake_seconds: int = 120,
    run_id: str = DEFAULT_TEST_RUN_ID,
) -> WorkflowInput:
    return WorkflowInput(
        order=OrderContext(order_id=order_id, customer_id=f"customer-{order_id}"),
        supervisor_instructions="Use deterministic Stage 2 test supervision.",
        demo_wake_interval_seconds=wake_seconds,
        run_id=run_id,
    )


def _event(event_id: str, event_type: EventType) -> OrderEvent:
    return OrderEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at="2026-09-04T00:00:00+00:00",
        payload={"source": "stage2-control-test"},
    )


async def _start_workflow(
    environment: WorkflowEnvironment,
    order_id: str,
    *,
    wake_seconds: int = 120,
    run_id: str = DEFAULT_TEST_RUN_ID,
) -> WorkflowHandleType:
    return await environment.client.start_workflow(
        OrderSupervisorWorkflow.run,
        _workflow_input(order_id, wake_seconds=wake_seconds, run_id=run_id),
        id=workflow_id_for_order(order_id),
        task_queue=TEST_TASK_QUEUE,
    )


async def _wait_for_snapshot(
    handle: WorkflowHandleType,
    predicate: SnapshotPredicate,
) -> WorkflowSnapshot:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QUERY_TIMEOUT_SECONDS
    last_snapshot: WorkflowSnapshot | None = None
    while loop.time() < deadline:
        last_snapshot = await handle.query(OrderSupervisorWorkflow.get_state)
        if predicate(last_snapshot):
            return last_snapshot
        await asyncio.sleep(0.01)
    pytest.fail(f"Workflow state did not converge; last snapshot: {last_snapshot!r}")


async def _wait_for_initial_sleep(handle: WorkflowHandleType) -> WorkflowSnapshot:
    return await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
    )


async def _terminate(handle: WorkflowHandleType, reason: str = "test cleanup") -> WorkflowSnapshot:
    await handle.signal(
        OrderSupervisorWorkflow.request_termination,
        TerminateRequest(reason=reason),
    )
    return await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)


def _requests_for(order_id: str) -> list[FakeSupervisorRequest]:
    return [request for request in SUPERVISOR_REQUESTS if request.order.order_id == order_id]


def _transitions_for(order_id: str) -> list[PersistenceTransitionRequest]:
    workflow_id = workflow_id_for_order(order_id)
    return [
        transition for transition in PERSISTED_TRANSITIONS if transition.workflow_id == workflow_id
    ]


async def test_instruction_is_retained_persisted_and_sent_to_next_review(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-instruction"
    handle = await _start_workflow(temporal_environment, order_id)
    await _wait_for_initial_sleep(handle)
    instruction = RunInstruction(
        instruction_id="instruction-speed",
        instruction="For this order, prioritize speed over cost.",
        created_at="2026-09-04T00:01:00+00:00",
    )

    await handle.signal(OrderSupervisorWorkflow.add_instruction, instruction)
    retained = await _wait_for_snapshot(
        handle,
        lambda state: (
            len(state.additional_instructions) == 1
            and any(
                transition.activity_type == "instruction_added"
                for transition in _transitions_for(order_id)
            )
        ),
    )
    assert retained.additional_instructions == [instruction]

    await handle.signal(
        OrderSupervisorWorkflow.receive_event,
        _event("instruction-delay", EventType.SHIPMENT_DELAYED),
    )
    await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 2 and state.is_sleeping,
    )
    event_request = next(
        request
        for request in _requests_for(order_id)
        if request.trigger == "event:shipment_delayed"
    )
    assert event_request.additional_instructions == [instruction]
    await _terminate(handle)


async def test_pause_queues_and_persists_event_until_resume(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-pause"
    handle = await _start_workflow(temporal_environment, order_id)
    initial = await _wait_for_initial_sleep(handle)

    await handle.signal(OrderSupervisorWorkflow.pause, PauseRequest(reason="Check inventory"))
    paused = await _wait_for_snapshot(handle, lambda state: state.paused)
    assert paused.workflow_status is WorkflowStatus.PAUSED
    assert paused.pause_reason == "Check inventory"

    paused_event = _event("paused-payment-failed", EventType.PAYMENT_FAILED)
    await handle.signal(OrderSupervisorWorkflow.receive_event, paused_event)
    queued = await _wait_for_snapshot(
        handle,
        lambda state: (
            state.pending_event_count == 1
            and any(
                transition.external_event_id == "paused-payment-failed"
                for transition in _transitions_for(order_id)
            )
        ),
    )
    await handle.signal(OrderSupervisorWorkflow.receive_event, paused_event)
    await _wait_for_snapshot(
        handle,
        lambda state: any(
            transition.activity_type == "duplicate_event_ignored"
            and transition.external_event_id == paused_event.event_id
            for transition in _transitions_for(order_id)
        ),
    )
    await temporal_environment.sleep(timedelta(seconds=1))
    still_paused = await handle.query(OrderSupervisorWorkflow.get_state)
    assert queued.order_state.event_count == 0
    assert still_paused.seen_event_count == 1
    assert still_paused.supervisor_invocation_count == initial.supervisor_invocation_count

    await handle.signal(OrderSupervisorWorkflow.resume, ResumeRequest())
    resumed = await _wait_for_snapshot(
        handle,
        lambda state: (
            state.pending_event_count == 0
            and state.order_state.payment == "failed"
            and state.supervisor_invocation_count == 2
            and state.is_sleeping
        ),
    )
    assert resumed.paused is False
    assert resumed.pause_reason is None
    await _terminate(handle)


async def test_interrupt_blocks_important_work_until_resume(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-interrupt"
    handle = await _start_workflow(temporal_environment, order_id)
    initial = await _wait_for_initial_sleep(handle)

    await handle.signal(
        OrderSupervisorWorkflow.interrupt,
        InterruptRequest(reason="Human review required"),
    )
    interrupted = await _wait_for_snapshot(handle, lambda state: state.interrupted)
    assert interrupted.workflow_status is WorkflowStatus.INTERRUPTED
    assert interrupted.interrupt_reason == "Human review required"

    await handle.signal(
        OrderSupervisorWorkflow.receive_event,
        _event("interrupted-delay", EventType.SHIPMENT_DELAYED),
    )
    await _wait_for_snapshot(handle, lambda state: state.pending_event_count == 1)
    await temporal_environment.sleep(timedelta(seconds=1))
    blocked = await handle.query(OrderSupervisorWorkflow.get_state)
    assert blocked.supervisor_invocation_count == initial.supervisor_invocation_count

    await handle.signal(OrderSupervisorWorkflow.resume, ResumeRequest(reason="Reviewed"))
    resumed = await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 2 and state.is_sleeping,
    )
    assert resumed.interrupted is False
    assert resumed.interrupt_reason is None
    await _terminate(handle)


async def test_overdue_blocked_timer_runs_one_catch_up_review(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-overdue"
    handle = await _start_workflow(temporal_environment, order_id, wake_seconds=30)
    initial = await _wait_for_initial_sleep(handle)
    await handle.signal(OrderSupervisorWorkflow.pause, PauseRequest(reason="Hold"))
    await _wait_for_snapshot(handle, lambda state: state.paused)

    await temporal_environment.sleep(timedelta(seconds=31))
    deferred = await _wait_for_snapshot(
        handle,
        lambda state: any(
            entry.entry_type == "scheduled_wake_deferred" for entry in state.recent_timeline
        ),
    )
    assert deferred.supervisor_invocation_count == initial.supervisor_invocation_count

    await handle.signal(OrderSupervisorWorkflow.resume, ResumeRequest())
    await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 2 and state.is_sleeping,
    )
    await temporal_environment.sleep(timedelta(seconds=1))
    after_catch_up = await handle.query(OrderSupervisorWorkflow.get_state)
    scheduled_requests = [
        request for request in _requests_for(order_id) if request.trigger == "scheduled_wake"
    ]
    assert after_catch_up.supervisor_invocation_count == 2
    assert len(scheduled_requests) == 1
    catch_up_entries = [
        entry
        for entry in after_catch_up.recent_timeline
        if entry.entry_type == "scheduled_wake"
        and entry.details.get("catch_up_after_block") is True
    ]
    assert len(catch_up_entries) == 1
    await _terminate(handle)


async def test_graceful_termination_flushes_terminal_transition(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-terminate"
    handle = await _start_workflow(temporal_environment, order_id)
    await _wait_for_initial_sleep(handle)

    result = await _terminate(handle, reason="Operator ended the demo")

    assert result.workflow_status is WorkflowStatus.TERMINATED
    assert result.completed is True
    assert result.next_wake_at is None
    assert result.paused is False
    assert result.interrupted is False
    assert result.completion_reason == "Terminated by operator: Operator ended the demo"
    transitions = _transitions_for(order_id)
    assert {"termination_requested", "workflow_terminated"}.issubset(
        {transition.activity_type for transition in transitions}
    )
    assert len({transition.activity_key for transition in transitions}) == len(transitions)
    assert all(
        transition.activity_key.startswith(
            f"{workflow_id_for_order(order_id)}:{DEFAULT_TEST_RUN_ID}:"
        )
        for transition in transitions
    )


async def test_repeated_order_runs_use_disjoint_run_scoped_activity_keys(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-repeated-order"
    first_run_id = "00000000-0000-0000-0000-000000000011"
    second_run_id = "00000000-0000-0000-0000-000000000012"

    first_handle = await _start_workflow(
        temporal_environment,
        order_id,
        run_id=first_run_id,
    )
    await _wait_for_initial_sleep(first_handle)
    await _terminate(first_handle, reason="First execution complete")

    second_handle = await _start_workflow(
        temporal_environment,
        order_id,
        run_id=second_run_id,
    )
    await _wait_for_initial_sleep(second_handle)
    await _terminate(second_handle, reason="Second execution complete")

    transitions = _transitions_for(order_id)
    first_keys = {
        transition.activity_key for transition in transitions if transition.run_id == first_run_id
    }
    second_keys = {
        transition.activity_key for transition in transitions if transition.run_id == second_run_id
    }

    assert first_keys
    assert second_keys
    assert first_keys.isdisjoint(second_keys)
    assert all(
        key.startswith(f"{workflow_id_for_order(order_id)}:{first_run_id}:") for key in first_keys
    )
    assert all(
        key.startswith(f"{workflow_id_for_order(order_id)}:{second_run_id}:") for key in second_keys
    )


async def test_persistence_recovers_after_one_exhausted_activity_retry_cycle(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage2-persistence-recovery"
    run_id = "00000000-0000-0000-0000-000000000021"
    first_activity_key = f"{workflow_id_for_order(order_id)}:{run_id}:00000001"
    PERSISTENCE_FAILURES_REMAINING[first_activity_key] = 3

    handle = await _start_workflow(
        temporal_environment,
        order_id,
        run_id=run_id,
    )
    await _wait_for_snapshot(
        handle,
        lambda state: (
            state.is_sleeping
            and PERSISTENCE_ATTEMPTS.get(first_activity_key) == 3
            and any(entry.entry_type == "persistence_fallback" for entry in state.recent_timeline)
        ),
    )
    await temporal_environment.sleep(timedelta(seconds=6))
    snapshot = await _wait_for_snapshot(
        handle,
        lambda state: (
            PERSISTENCE_ATTEMPTS.get(first_activity_key) == 4
            and any(
                transition.activity_key == first_activity_key
                for transition in _transitions_for(order_id)
            )
        ),
    )

    assert PERSISTENCE_ATTEMPTS[first_activity_key] == 4
    assert (
        len(
            [
                transition
                for transition in _transitions_for(order_id)
                if transition.activity_key == first_activity_key
            ]
        )
        == 1
    )
    assert any(entry.entry_type == "persistence_fallback" for entry in snapshot.recent_timeline)
    await _terminate(handle)


async def test_terminal_or_terminating_workflow_ignores_late_signals() -> None:
    event = _event("late-event", EventType.PAYMENT_FAILED)
    instruction = RunInstruction(
        instruction_id="late-instruction",
        instruction="This must not be retained after termination begins.",
        created_at="2026-09-04T00:02:00+00:00",
    )

    completed_workflow = OrderSupervisorWorkflow(_workflow_input("stage2-late-completed"))
    completed_workflow._status = WorkflowStatus.COMPLETED
    completed_workflow.receive_event(event)
    completed_workflow.add_instruction(instruction)

    terminating_workflow = OrderSupervisorWorkflow(_workflow_input("stage2-late-terminating"))
    terminating_workflow._termination_requested = True
    terminating_workflow.receive_event(event)
    terminating_workflow.add_instruction(instruction)

    assert completed_workflow._pending_events == []
    assert completed_workflow._additional_instructions == []
    assert terminating_workflow._pending_events == []
    assert terminating_workflow._additional_instructions == []
