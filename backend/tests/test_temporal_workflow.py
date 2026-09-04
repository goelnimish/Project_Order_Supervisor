"""Focused integration tests for the Stage 1 Temporal workflow."""

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.temporal.activities import (
    FAKE_SUPERVISOR_ACTIVITY_NAME,
    fake_supervisor,
    generate_final_output,
)
from app.temporal.models import (
    EventType,
    FakeSupervisorDecision,
    FakeSupervisorRequest,
    OrderContext,
    OrderEvent,
    TimelineEntry,
    WorkflowInput,
    WorkflowSnapshot,
    WorkflowStatus,
    workflow_id_for_order,
)
from app.temporal.workflows import DEFAULT_WAKE_SECONDS, OrderSupervisorWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_TASK_QUEUE = "order-supervisor-stage1-tests"
FAILING_ACTIVITY_TASK_QUEUE = "order-supervisor-stage1-failing-activity-tests"
TEST_WAKE_SECONDS = 30
QUERY_TIMEOUT_SECONDS = 5.0
QUERY_POLL_SECONDS = 0.01

SnapshotPredicate = Callable[[WorkflowSnapshot], bool]
WorkflowHandleType = WorkflowHandle[OrderSupervisorWorkflow, WorkflowSnapshot]
FAILED_SUPERVISOR_REQUESTS: list[FakeSupervisorRequest] = []


@activity.defn(name=FAKE_SUPERVISOR_ACTIVITY_NAME)
async def failing_fake_supervisor(
    request: FakeSupervisorRequest,
) -> FakeSupervisorDecision:
    """Record the request, then fail so retry exhaustion and fallback can be tested."""

    FAILED_SUPERVISOR_REQUESTS.append(request)
    raise ApplicationError("Intentional fake-supervisor failure", type="TestFailure")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_environment() -> WorkflowEnvironment:
    """Run one isolated time-skipping server and Worker for this test module."""

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[fake_supervisor, generate_final_output],
        ):
            yield environment


def _workflow_input(
    order_id: str,
    *,
    initial_state: dict[str, object] | None = None,
) -> WorkflowInput:
    return WorkflowInput(
        order=OrderContext(
            order_id=order_id,
            customer_id=f"customer-{order_id}",
            initial_state=initial_state or {},
        ),
        supervisor_instructions="Keep this Stage 1 test order moving safely.",
        demo_wake_interval_seconds=TEST_WAKE_SECONDS,
    )


def _event(event_id: str, event_type: EventType) -> OrderEvent:
    return OrderEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at="2026-09-04T00:00:00+00:00",
        payload={"source": "temporal-test"},
    )


async def _start_workflow(
    environment: WorkflowEnvironment,
    order_id: str,
    *,
    initial_state: dict[str, object] | None = None,
) -> WorkflowHandleType:
    return await environment.client.start_workflow(
        OrderSupervisorWorkflow.run,
        _workflow_input(order_id, initial_state=initial_state),
        id=workflow_id_for_order(order_id),
        task_queue=TEST_TASK_QUEUE,
    )


async def _wait_for_snapshot(
    handle: WorkflowHandleType,
    predicate: SnapshotPredicate,
) -> WorkflowSnapshot:
    """Poll the read-only Query until a preceding Signal/Activity is observable."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + QUERY_TIMEOUT_SECONDS
    last_snapshot: WorkflowSnapshot | None = None

    while loop.time() < deadline:
        last_snapshot = await handle.query(OrderSupervisorWorkflow.get_state)
        if predicate(last_snapshot):
            return last_snapshot
        await asyncio.sleep(QUERY_POLL_SECONDS)

    pytest.fail(f"Workflow state did not converge in time; last snapshot: {last_snapshot!r}")


async def _wait_for_initial_sleep(handle: WorkflowHandleType) -> WorkflowSnapshot:
    return await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
    )


async def _complete_with_delivery(
    environment: WorkflowEnvironment,
    handle: WorkflowHandleType,
    event_id: str,
) -> WorkflowSnapshot:
    """Finish a test workflow through its deterministic delivered-event rule."""

    current = await handle.query(OrderSupervisorWorkflow.get_state)
    if current.completed:
        return current

    await handle.signal(
        OrderSupervisorWorkflow.receive_event,
        _event(event_id, EventType.DELIVERED),
    )
    # Awaiting a result normally enables automatic time skipping. Disable it here so
    # a recurring review timer cannot advance while the delivered Signal is processed.
    with environment.auto_time_skipping_disabled():
        return await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)


def _entries(
    snapshot: WorkflowSnapshot,
    entry_type: str,
    *,
    event_id: str | None = None,
) -> list[TimelineEntry]:
    return [
        entry
        for entry in snapshot.recent_timeline
        if entry.entry_type == entry_type
        and (event_id is None or entry.details.get("event_id") == event_id)
    ]


async def test_workflow_starts_and_runs_initial_supervisor(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-start")
    try:
        snapshot = await _wait_for_initial_sleep(handle)

        assert handle.id == "order-supervisor:test-start"
        assert snapshot.order_id == "test-start"
        assert snapshot.workflow_status is WorkflowStatus.SLEEPING
        assert snapshot.supervisor_invocation_count == 1
        assert snapshot.next_wake_at is not None
        assert snapshot.completed is False
        assert _entries(snapshot, "workflow_started")
        decisions = _entries(snapshot, "supervisor_decision")
        assert len(decisions) == 1
        assert decisions[0].details["trigger"] == "workflow_start"
        assert _entries(snapshot, "sleep_scheduled")
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-start")


async def test_routine_signal_does_not_invoke_supervisor(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-routine")
    try:
        initial = await _wait_for_initial_sleep(handle)
        event = _event("routine-payment-confirmed", EventType.PAYMENT_CONFIRMED)

        await handle.signal(OrderSupervisorWorkflow.receive_event, event)
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: state.order_state.event_count == 1 and state.pending_event_count == 0,
        )

        assert snapshot.order_state.payment == "confirmed"
        assert snapshot.order_state.last_event_type is EventType.PAYMENT_CONFIRMED
        assert snapshot.supervisor_invocation_count == initial.supervisor_invocation_count
        assert len(_entries(snapshot, "order_event_received", event_id=event.event_id)) == 1
        assert len(_entries(snapshot, "supervisor_wake_suppressed", event_id=event.event_id)) == 1
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-routine")


async def test_important_signal_invokes_supervisor(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-important")
    try:
        initial = await _wait_for_initial_sleep(handle)
        event = _event("important-shipment-delayed", EventType.SHIPMENT_DELAYED)

        await handle.signal(OrderSupervisorWorkflow.receive_event, event)
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: (
                state.order_state.event_count == 1
                and state.supervisor_invocation_count == 2
                and state.is_sleeping
            ),
        )

        assert snapshot.order_state.shipment == "delayed"
        assert snapshot.supervisor_invocation_count == initial.supervisor_invocation_count + 1
        assert len(_entries(snapshot, "supervisor_wake_requested", event_id=event.event_id)) == 1
        event_decisions = [
            entry
            for entry in _entries(snapshot, "supervisor_decision")
            if entry.details["trigger"] == "event:shipment_delayed"
        ]
        assert len(event_decisions) == 1
        assert event_decisions[0].details["should_act"] is True
        assert "message_logistics_team" in event_decisions[0].details["proposed_action_names"]
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-important")


async def test_scheduled_wake_invokes_supervisor(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-scheduled-wake")
    try:
        initial = await _wait_for_initial_sleep(handle)

        await temporal_environment.sleep(timedelta(seconds=TEST_WAKE_SECONDS + 1))
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: state.supervisor_invocation_count >= 2 and state.is_sleeping,
        )

        assert snapshot.supervisor_invocation_count == initial.supervisor_invocation_count + 1
        scheduled_decisions = [
            entry
            for entry in _entries(snapshot, "supervisor_decision")
            if entry.details["trigger"] == "scheduled_wake"
        ]
        assert len(scheduled_decisions) == 1
        assert scheduled_decisions[0].details["should_act"] is False
        assert snapshot.next_wake_at is not None
        assert snapshot.next_wake_at != initial.next_wake_at
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-scheduled")


async def test_duplicate_event_id_is_processed_only_once(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-duplicate")
    try:
        initial = await _wait_for_initial_sleep(handle)
        event = _event("duplicate-shipment-delayed", EventType.SHIPMENT_DELAYED)

        await handle.signal(OrderSupervisorWorkflow.receive_event, event)
        await handle.signal(OrderSupervisorWorkflow.receive_event, event)
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 2
                and bool(_entries(state, "duplicate_event_ignored", event_id=event.event_id))
            ),
        )

        assert snapshot.seen_event_count == 1
        assert snapshot.order_state.event_count == 1
        assert snapshot.supervisor_invocation_count == initial.supervisor_invocation_count + 1
        assert len(_entries(snapshot, "order_event_received", event_id=event.event_id)) == 1
        assert len(_entries(snapshot, "duplicate_event_ignored", event_id=event.event_id)) == 1
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-duplicate")


async def test_delivered_causes_deterministic_completion(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-delivered")
    initial = await _wait_for_initial_sleep(handle)
    delivered = _event("terminal-delivered", EventType.DELIVERED)

    await handle.signal(OrderSupervisorWorkflow.receive_event, delivered)
    with temporal_environment.auto_time_skipping_disabled():
        result = await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)

    assert result.completed is True
    assert result.workflow_status is WorkflowStatus.COMPLETED
    assert result.order_state.lifecycle == "completed"
    assert result.order_state.shipment == "delivered"
    assert result.order_state.last_event_type is EventType.DELIVERED
    assert result.supervisor_invocation_count == initial.supervisor_invocation_count
    assert result.next_wake_at is None
    assert result.completion_reason == "Order delivered (event_id=terminal-delivered)."
    completed_entries = _entries(result, "workflow_completed", event_id=delivered.event_id)
    assert len(completed_entries) == 1
    assert completed_entries[0].details["rule"] == "terminal_delivered_event"


async def test_two_orders_run_independently(
    temporal_environment: WorkflowEnvironment,
) -> None:
    first = await _start_workflow(temporal_environment, "test-independent-a")
    second = await _start_workflow(temporal_environment, "test-independent-b")
    try:
        await _wait_for_initial_sleep(first)
        await _wait_for_initial_sleep(second)

        await first.signal(
            OrderSupervisorWorkflow.receive_event,
            _event("first-payment-failed", EventType.PAYMENT_FAILED),
        )
        await second.signal(
            OrderSupervisorWorkflow.receive_event,
            _event("second-shipment-created", EventType.SHIPMENT_CREATED),
        )
        first_snapshot = await _wait_for_snapshot(
            first,
            lambda state: (
                state.order_state.payment == "failed" and state.supervisor_invocation_count == 2
            ),
        )
        second_snapshot = await _wait_for_snapshot(
            second,
            lambda state: (
                state.order_state.shipment == "created" and state.order_state.event_count == 1
            ),
        )

        assert first.id == "order-supervisor:test-independent-a"
        assert second.id == "order-supervisor:test-independent-b"
        assert first_snapshot.order_id == "test-independent-a"
        assert second_snapshot.order_id == "test-independent-b"
        assert first_snapshot.supervisor_invocation_count == 2
        assert second_snapshot.supervisor_invocation_count == 1
        assert first_snapshot.order_state.shipment == "not_created"
        assert second_snapshot.order_state.payment == "pending"
    finally:
        await _complete_with_delivery(temporal_environment, first, "cleanup-independent-a")
        await _complete_with_delivery(temporal_environment, second, "cleanup-independent-b")


async def test_workflow_query_returns_expected_state(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(
        temporal_environment,
        "test-query",
        initial_state={"sales_channel": "web"},
    )
    try:
        await _wait_for_initial_sleep(handle)
        event = _event("query-shipment-created", EventType.SHIPMENT_CREATED)
        await handle.signal(OrderSupervisorWorkflow.receive_event, event)
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: state.order_state.last_event_type is EventType.SHIPMENT_CREATED,
        )

        assert isinstance(snapshot, WorkflowSnapshot)
        assert snapshot.order_id == "test-query"
        assert snapshot.workflow_status is WorkflowStatus.SLEEPING
        assert snapshot.order_state.attributes["sales_channel"] == "web"
        assert snapshot.order_state.attributes["last_event_payload"] == {"source": "temporal-test"}
        assert snapshot.pending_event_count == 0
        assert snapshot.seen_event_count == 1
        assert snapshot.is_sleeping is True
        assert snapshot.next_wake_at is not None
        assert snapshot.supervisor_invocation_count == 1
        assert snapshot.completed is False
        assert snapshot.completion_reason is None
        assert [entry.sequence for entry in snapshot.recent_timeline] == list(
            range(1, len(snapshot.recent_timeline) + 1)
        )
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-query")


async def test_blank_order_id_fails_non_retryably(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await temporal_environment.client.start_workflow(
        OrderSupervisorWorkflow.run,
        _workflow_input(""),
        id="order-supervisor:test-invalid-input",
        task_queue=TEST_TASK_QUEUE,
    )

    with temporal_environment.auto_time_skipping_disabled():
        with pytest.raises(WorkflowFailureError) as raised:
            await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)

    cause = raised.value.__cause__
    assert isinstance(cause, ApplicationError)
    assert cause.type == "InvalidWorkflowInput"
    assert cause.non_retryable is True


async def test_novel_event_type_is_safely_treated_as_unknown(
    temporal_environment: WorkflowEnvironment,
) -> None:
    handle = await _start_workflow(temporal_environment, "test-novel-event")
    try:
        await _wait_for_initial_sleep(handle)
        novel_event = OrderEvent(
            event_id="future-carrier-exception",
            event_type="future_carrier_exception",  # type: ignore[arg-type]
            occurred_at="2026-09-04T00:00:00+00:00",
            payload={"source": "future-integration"},
        )

        await handle.signal(OrderSupervisorWorkflow.receive_event, novel_event)
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: (
                state.order_state.last_event_type is EventType.UNKNOWN
                and state.supervisor_invocation_count == 2
                and state.is_sleeping
            ),
        )

        assert (
            len(_entries(snapshot, "supervisor_wake_requested", event_id=novel_event.event_id)) == 1
        )
        decisions = [
            entry
            for entry in _entries(snapshot, "supervisor_decision")
            if entry.details["trigger"] == "event:unknown"
        ]
        assert len(decisions) == 1
        assert decisions[0].details["proposed_action_names"] == ["create_internal_note"]
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-novel-event")


async def test_activity_failures_exhaust_retries_then_use_safe_fallback() -> None:
    FAILED_SUPERVISOR_REQUESTS.clear()

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=FAILING_ACTIVITY_TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[failing_fake_supervisor, generate_final_output],
        ):
            handle = await environment.client.start_workflow(
                OrderSupervisorWorkflow.run,
                _workflow_input("test-activity-fallback"),
                id="order-supervisor:test-activity-fallback",
                task_queue=FAILING_ACTIVITY_TASK_QUEUE,
            )

            await environment.sleep(timedelta(seconds=10))
            snapshot = await _wait_for_snapshot(
                handle,
                lambda state: (
                    state.is_sleeping
                    and bool(_entries(state, "supervisor_fallback"))
                    and state.supervisor_invocation_count == 1
                ),
            )

            assert len(FAILED_SUPERVISOR_REQUESTS) == 3
            assert all(
                request.order.order_id == "test-activity-fallback"
                and request.supervisor_instructions == "Keep this Stage 1 test order moving safely."
                for request in FAILED_SUPERVISOR_REQUESTS
            )
            assert snapshot.next_wake_at is not None
            assert snapshot.completed is False

            await _complete_with_delivery(
                environment,
                handle,
                "cleanup-activity-fallback",
            )


async def test_nullable_wake_interval_uses_deterministic_default(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "test-nullable-wake"
    workflow_input = _workflow_input(order_id)
    workflow_input.demo_wake_interval_seconds = None
    handle = await temporal_environment.client.start_workflow(
        OrderSupervisorWorkflow.run,
        workflow_input,
        id=workflow_id_for_order(order_id),
        task_queue=TEST_TASK_QUEUE,
    )

    try:
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
        )
        sleep_entry = _entries(snapshot, "sleep_scheduled")[-1]
        assert sleep_entry.details["wake_seconds"] == DEFAULT_WAKE_SECONDS
    finally:
        await _complete_with_delivery(temporal_environment, handle, "cleanup-nullable-wake")
