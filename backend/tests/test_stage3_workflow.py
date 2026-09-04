"""Focused time-skipping coverage for Stage 3 Workflow orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.temporal.models import (
    AgentDecisionData,
    BusinessActionExecutionRequest,
    BusinessActionExecutionResult,
    BusinessActionProposalData,
    CompactMemoryData,
    EventType,
    FinalOutputActivityResult,
    FinalOutputData,
    FinalOutputRequest,
    OrderContext,
    OrderEvent,
    PersistenceTransitionRequest,
    RunInstruction,
    SupervisorRequest,
    TerminateRequest,
    WorkflowInput,
    WorkflowSnapshot,
    WorkflowStatus,
    workflow_id_for_order,
)
from app.temporal.workflows import (
    EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME,
    FAKE_SUPERVISOR_ACTIVITY_NAME,
    FINAL_OUTPUT_ACTIVITY_NAME,
    PERSIST_TRANSITION_ACTIVITY_NAME,
    SUPERVISOR_CONTEXT_ACTIVITY_LIMIT,
    OrderSupervisorWorkflow,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_TASK_QUEUE = "order-supervisor-stage3-workflow-tests"
QUERY_TIMEOUT_SECONDS = 5.0
TEST_WAKE_SECONDS = 3_600

DecisionBuilder = Callable[[SupervisorRequest], AgentDecisionData]

DECISION_BUILDERS: dict[str, DecisionBuilder] = {}
SUPERVISOR_REQUESTS: list[SupervisorRequest] = []
SUPERVISOR_ATTEMPTS: dict[str, int] = {}
FAIL_SUPERVISOR_FOR: set[str] = set()
ACTION_REQUESTS: list[BusinessActionExecutionRequest] = []
FINAL_OUTPUT_REQUESTS: list[FinalOutputRequest] = []
FINAL_OUTPUT_ATTEMPTS: dict[str, int] = {}
FAIL_FINAL_OUTPUT_FOR: set[str] = set()
PERSISTED_TRANSITIONS: list[PersistenceTransitionRequest] = []


def _copy_memory(memory: CompactMemoryData | None) -> CompactMemoryData:
    if memory is None:
        return CompactMemoryData(
            order_state="Order supervision began.",
            important_facts=["Initial supervisor review completed."],
            next_review=f"Review again in {TEST_WAKE_SECONDS} seconds.",
        )
    return CompactMemoryData(
        order_state=memory.order_state,
        important_facts=list(memory.important_facts),
        open_issues=list(memory.open_issues),
        actions_taken=list(memory.actions_taken),
        active_constraints=list(memory.active_constraints),
        next_review=memory.next_review,
    )


def _default_decision(request: SupervisorRequest) -> AgentDecisionData:
    return AgentDecisionData(
        trigger=request.trigger,
        decision_summary=f"Stub reviewed {request.trigger}.",
        should_act=False,
        actions=[],
        memory_update=_copy_memory(request.memory_summary),
        next_wake_seconds=request.next_wake_seconds,
        completion_recommendation=False,
        urgency="low",
        provider="deterministic",
        model="stage3-workflow-stub",
    )


@activity.defn(name=FAKE_SUPERVISOR_ACTIVITY_NAME)
async def supervisor_stub(request: SupervisorRequest) -> AgentDecisionData:
    order_id = request.order.order_id
    SUPERVISOR_ATTEMPTS[order_id] = SUPERVISOR_ATTEMPTS.get(order_id, 0) + 1
    SUPERVISOR_REQUESTS.append(request)
    if order_id in FAIL_SUPERVISOR_FOR:
        raise ApplicationError(
            "Configured supervisor unavailable.",
            type="SupervisorProviderUnavailable",
        )
    builder = DECISION_BUILDERS.get(order_id, _default_decision)
    return builder(request)


@activity.defn(name=EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME)
async def action_stub(
    request: BusinessActionExecutionRequest,
) -> BusinessActionExecutionResult:
    ACTION_REQUESTS.append(request)
    return BusinessActionExecutionResult(
        action_name=request.action_name,
        idempotency_key=request.idempotency_key,
        status="executed",
        summary=f"Executed stub {request.action_name}.",
        persisted=True,
        details={"new_record": True},
    )


@activity.defn(name=FINAL_OUTPUT_ACTIVITY_NAME)
async def final_output_stub(request: FinalOutputRequest) -> FinalOutputActivityResult:
    order_id = request.order.order_id
    FINAL_OUTPUT_REQUESTS.append(request)
    FINAL_OUTPUT_ATTEMPTS[order_id] = FINAL_OUTPUT_ATTEMPTS.get(order_id, 0) + 1
    if order_id in FAIL_FINAL_OUTPUT_FOR:
        raise RuntimeError("Intentional final-output failure")
    return FinalOutputActivityResult(
        output=FinalOutputData(
            final_summary=f"Stub final output for {order_id} ({request.completion_status.value}).",
            important_actions=["Reviewed the persistent action history."],
            key_learnings=["Temporal lifecycle rules authorized finalization."],
            recommendations=["Retain the audit timeline."],
        ),
        provider="deterministic",
        model="stage3-workflow-stub",
    )


@activity.defn(name=PERSIST_TRANSITION_ACTIVITY_NAME)
async def persistence_stub(request: PersistenceTransitionRequest) -> None:
    PERSISTED_TRANSITIONS.append(request)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_environment() -> WorkflowEnvironment:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[OrderSupervisorWorkflow],
            activities=[
                supervisor_stub,
                action_stub,
                final_output_stub,
                persistence_stub,
            ],
        ):
            yield environment


def _workflow_input(
    order_id: str,
    *,
    run_id: str,
    available_actions: list[str] | None = None,
    provider: str = "deterministic",
) -> WorkflowInput:
    return WorkflowInput(
        order=OrderContext(
            order_id=order_id,
            customer_id=f"customer-{order_id}",
            initial_state={"source": "stage3-workflow-test"},
        ),
        supervisor_instructions="Exercise Stage 3 orchestration deterministically.",
        demo_wake_interval_seconds=TEST_WAKE_SECONDS,
        run_id=run_id,
        available_actions=(
            list(available_actions)
            if available_actions is not None
            else [
                "message_fulfillment_team",
                "message_payments_team",
                "message_logistics_team",
                "message_customer",
                "create_internal_note",
            ]
        ),
        supervisor_provider=provider,
        supervisor_model="qwen3:1.7b" if provider == "ollama" else None,
        wake_aggressiveness="moderate",
    )


async def _start_workflow(
    environment: WorkflowEnvironment,
    order_id: str,
    *,
    run_id: str,
    available_actions: list[str] | None = None,
    provider: str = "deterministic",
):
    return await environment.client.start_workflow(
        OrderSupervisorWorkflow.run,
        _workflow_input(
            order_id,
            run_id=run_id,
            available_actions=available_actions,
            provider=provider,
        ),
        id=workflow_id_for_order(order_id),
        task_queue=TEST_TASK_QUEUE,
    )


def _event(event_id: str, event_type: EventType) -> OrderEvent:
    return OrderEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at="2026-09-04T12:00:00+00:00",
        payload={"source": "stage3-workflow-test"},
    )


async def _wait_for_snapshot(handle, predicate) -> WorkflowSnapshot:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QUERY_TIMEOUT_SECONDS
    last_snapshot: WorkflowSnapshot | None = None
    while loop.time() < deadline:
        last_snapshot = await handle.query(OrderSupervisorWorkflow.get_state)
        if predicate(last_snapshot):
            return last_snapshot
        await asyncio.sleep(0.01)
    pytest.fail(f"Workflow state did not converge; last snapshot: {last_snapshot!r}")


async def _wait_for_initial_sleep(handle) -> WorkflowSnapshot:
    return await _wait_for_snapshot(
        handle,
        lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
    )


async def _terminate(handle, reason: str = "Stage 3 test cleanup") -> WorkflowSnapshot:
    await handle.signal(
        OrderSupervisorWorkflow.request_termination,
        TerminateRequest(reason=reason),
    )
    return await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)


def _supervisor_requests_for(order_id: str) -> list[SupervisorRequest]:
    return [request for request in SUPERVISOR_REQUESTS if request.order.order_id == order_id]


def _action_requests_for(order_id: str) -> list[BusinessActionExecutionRequest]:
    workflow_id = workflow_id_for_order(order_id)
    return [request for request in ACTION_REQUESTS if request.workflow_id == workflow_id]


def _final_requests_for(order_id: str) -> list[FinalOutputRequest]:
    return [request for request in FINAL_OUTPUT_REQUESTS if request.order.order_id == order_id]


def _transitions_for(order_id: str) -> list[PersistenceTransitionRequest]:
    workflow_id = workflow_id_for_order(order_id)
    return [
        transition for transition in PERSISTED_TRANSITIONS if transition.workflow_id == workflow_id
    ]


def _action_decision(
    request: SupervisorRequest,
    *,
    action_name: str = "message_logistics_team",
    completion_recommendation: bool = False,
) -> AgentDecisionData:
    if request.event_type is not EventType.SHIPMENT_DELAYED:
        decision = _default_decision(request)
        decision.completion_recommendation = completion_recommendation
        return decision
    return AgentDecisionData(
        trigger=request.trigger,
        decision_summary="The shipment delay needs a logistics response.",
        should_act=True,
        actions=[
            BusinessActionProposalData(
                action_name=action_name,
                arguments={"content": "Please investigate this delayed shipment."},
            )
        ],
        memory_update=CompactMemoryData(
            order_state="The shipment is delayed.",
            important_facts=["A shipment delay was reported."],
            open_issues=["Logistics follow-up is outstanding."],
            next_review="Review after logistics responds.",
        ),
        next_wake_seconds=request.next_wake_seconds,
        completion_recommendation=completion_recommendation,
        urgency="high",
        provider="deterministic",
        model="stage3-workflow-stub",
    )


async def test_compact_memory_persists_into_bounded_future_supervisor_context(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-memory-context"
    run_id = "00000000-0000-0000-0000-000000000301"

    def memory_builder(request: SupervisorRequest) -> AgentDecisionData:
        memory = _copy_memory(request.memory_summary)
        if request.trigger == "workflow_start":
            memory.important_facts = ["Memory created during the initial review."]
        elif "Memory created during the initial review." not in memory.important_facts:
            memory.important_facts.append("Memory unexpectedly missing.")
        return AgentDecisionData(
            trigger=request.trigger,
            decision_summary=f"Reviewed {request.trigger} with compact memory.",
            should_act=False,
            actions=[],
            memory_update=memory,
            next_wake_seconds=request.next_wake_seconds,
            completion_recommendation=False,
            urgency="low",
            provider="deterministic",
            model="stage3-workflow-stub",
        )

    DECISION_BUILDERS[order_id] = memory_builder
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    try:
        await _wait_for_initial_sleep(handle)
        instruction = RunInstruction(
            instruction_id="memory-context-instruction",
            instruction="Keep the next review concise.",
            created_at="2026-09-04T12:01:00+00:00",
        )
        await handle.signal(OrderSupervisorWorkflow.add_instruction, instruction)
        for index in range(12):
            await handle.signal(
                OrderSupervisorWorkflow.receive_event,
                _event(f"memory-routine-{index}", EventType.PAYMENT_CONFIRMED),
            )
        await handle.signal(
            OrderSupervisorWorkflow.receive_event,
            _event("memory-important-delay", EventType.SHIPMENT_DELAYED),
        )

        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 2
                and state.is_sleeping
                and len(
                    [
                        transition
                        for transition in _transitions_for(order_id)
                        if transition.activity_type == "memory_updated"
                    ]
                )
                >= 2
            ),
        )
        requests = _supervisor_requests_for(order_id)
        future_request = requests[-1]

        assert future_request.trigger == "event:shipment_delayed"
        assert future_request.memory_summary is not None
        assert future_request.memory_summary.important_facts == [
            "Memory created during the initial review."
        ]
        assert future_request.additional_instructions == [instruction]
        assert len(future_request.recent_activity) == SUPERVISOR_CONTEXT_ACTIVITY_LIMIT
        assert snapshot.memory_summary is not None
        assert snapshot.memory_summary.important_facts == [
            "Memory created during the initial review."
        ]

        memory_transitions = [
            transition
            for transition in _transitions_for(order_id)
            if transition.activity_type == "memory_updated"
        ]
        assert len(memory_transitions) >= 2
        assert memory_transitions[-1].memory_summary is not None
        assert memory_transitions[-1].memory_summary["important_facts"] == [
            "Memory created during the initial review."
        ]
    finally:
        await _terminate(handle)


async def test_enabled_action_uses_deterministic_idempotency_key(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-enabled-action"
    run_id = "00000000-0000-0000-0000-000000000302"
    DECISION_BUILDERS[order_id] = _action_decision
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    try:
        await _wait_for_initial_sleep(handle)
        await handle.signal(
            OrderSupervisorWorkflow.receive_event,
            _event("enabled-action-delay", EventType.SHIPMENT_DELAYED),
        )
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: state.supervisor_invocation_count == 2 and state.is_sleeping,
        )

        requests = _action_requests_for(order_id)
        assert len(requests) == 1
        action_request = requests[0]
        expected_key = f"{workflow_id_for_order(order_id)}:{run_id}:action:00000002:0000"
        assert action_request.idempotency_key == expected_key
        assert action_request.run_id == run_id
        assert action_request.supervisor_invocation == 2
        assert action_request.action_index == 0
        assert action_request.action_name == "message_logistics_team"
        assert action_request.trigger == "event:shipment_delayed"
        assert snapshot.memory_summary is not None
        assert "message_logistics_team" in snapshot.memory_summary.actions_taken
        executed_entries = [
            entry
            for entry in snapshot.recent_timeline
            if entry.entry_type == "business_action_executed"
        ]
        assert len(executed_entries) == 1
        assert executed_entries[0].details["idempotency_key"] == expected_key
    finally:
        await _terminate(handle)


async def test_scheduled_wake_suppresses_only_repeated_action_until_new_event(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage46-semantic-action-guard"
    run_id = "00000000-0000-0000-0000-000000000346"

    def duplicate_proposal_builder(request: SupervisorRequest) -> AgentDecisionData:
        decision = _default_decision(request)
        action_names: list[str] = []
        if request.event_type is EventType.SHIPMENT_DELAYED:
            action_names = ["message_logistics_team"]
        elif request.trigger == "scheduled_wake":
            action_names = ["message_logistics_team", "message_customer"]
        if not action_names:
            return decision

        decision.decision_summary = f"Proposed actions for {request.trigger}."
        decision.should_act = True
        decision.actions = [
            BusinessActionProposalData(
                action_name=action_name,
                arguments={"content": f"Proposed {action_name} during {request.trigger}."},
            )
            for action_name in action_names
        ]
        decision.urgency = "high"
        return decision

    DECISION_BUILDERS[order_id] = duplicate_proposal_builder
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    try:
        await _wait_for_initial_sleep(handle)
        first_event = _event("semantic-delay-e1", EventType.SHIPMENT_DELAYED)
        await handle.signal(OrderSupervisorWorkflow.receive_event, first_event)
        await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 2
                and state.is_sleeping
                and len(_action_requests_for(order_id)) == 1
            ),
        )

        first_request = _action_requests_for(order_id)[0]
        assert first_request.action_name == "message_logistics_team"
        assert first_request.trigger == "event:shipment_delayed"

        await temporal_environment.sleep(timedelta(seconds=TEST_WAKE_SECONDS + 1))
        await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 3
                and state.is_sleeping
                and len(_action_requests_for(order_id)) == 2
            ),
        )

        after_scheduled = _action_requests_for(order_id)
        assert [request.action_name for request in after_scheduled] == [
            "message_logistics_team",
            "message_customer",
        ]
        assert after_scheduled[1].supervisor_invocation == 3
        assert after_scheduled[1].action_index == 1
        assert after_scheduled[1].trigger == "scheduled_wake"

        suppressions = [
            transition
            for transition in _transitions_for(order_id)
            if transition.activity_type == "business_action_suppressed"
        ]
        assert len(suppressions) == 1
        suppression = suppressions[0]
        assert suppression.status == "suppressed"
        assert suppression.action_name == "message_logistics_team"
        assert suppression.event_name == "shipment_delayed"
        assert suppression.external_event_id == first_event.event_id
        assert suppression.payload["validation_outcome"] == "semantic_duplicate"
        assert suppression.payload["reason"] == "same_action_same_causal_event"
        assert suppression.payload["trigger"] == "scheduled_wake"
        assert suppression.payload["original_idempotency_key"] == first_request.idempotency_key

        await handle.signal(OrderSupervisorWorkflow.receive_event, first_event)
        duplicate_snapshot = await _wait_for_snapshot(
            handle,
            lambda state: any(
                entry.entry_type == "duplicate_event_ignored"
                and entry.details.get("event_id") == first_event.event_id
                for entry in state.recent_timeline
            ),
        )
        assert duplicate_snapshot.supervisor_invocation_count == 3
        assert len(_action_requests_for(order_id)) == 2

        second_event = _event("semantic-delay-e2", EventType.SHIPMENT_DELAYED)
        await handle.signal(OrderSupervisorWorkflow.receive_event, second_event)
        await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 4
                and state.is_sleeping
                and len(_action_requests_for(order_id)) == 3
            ),
        )

        final_requests = _action_requests_for(order_id)
        assert [request.action_name for request in final_requests] == [
            "message_logistics_team",
            "message_customer",
            "message_logistics_team",
        ]
        assert final_requests[2].supervisor_invocation == 4
        assert final_requests[2].trigger == "event:shipment_delayed"
        assert len({request.idempotency_key for request in final_requests}) == 3
    finally:
        await _terminate(handle)


async def test_disabled_action_is_rejected_without_execution(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-disabled-action"
    run_id = "00000000-0000-0000-0000-000000000303"

    def misleading_action_decision(request: SupervisorRequest) -> AgentDecisionData:
        decision = _action_decision(request)
        if request.event_type is EventType.SHIPMENT_DELAYED:
            decision.memory_update.actions_taken = ["message_logistics_team"]
        return decision

    DECISION_BUILDERS[order_id] = misleading_action_decision
    handle = await _start_workflow(
        temporal_environment,
        order_id,
        run_id=run_id,
        available_actions=["create_internal_note"],
    )
    try:
        await _wait_for_initial_sleep(handle)
        await handle.signal(
            OrderSupervisorWorkflow.receive_event,
            _event("disabled-action-delay", EventType.SHIPMENT_DELAYED),
        )
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 2
                and any(
                    entry.entry_type == "business_action_rejected"
                    for entry in state.recent_timeline
                )
                and state.is_sleeping
            ),
        )

        assert _action_requests_for(order_id) == []
        rejected = [
            entry
            for entry in snapshot.recent_timeline
            if entry.entry_type == "business_action_rejected"
        ]
        assert len(rejected) == 1
        assert rejected[0].details == {
            "action_name": "message_logistics_team",
            "validation_outcome": "disabled_action",
            "trigger": "event:shipment_delayed",
        }
        persisted_rejections = [
            transition
            for transition in _transitions_for(order_id)
            if transition.activity_type == "business_action_rejected"
        ]
        assert len(persisted_rejections) == 1
        assert persisted_rejections[0].status == "rejected"
        assert persisted_rejections[0].action_name == "message_logistics_team"
        assert snapshot.memory_summary is not None
        assert snapshot.memory_summary.actions_taken == []
    finally:
        await _terminate(handle)


async def test_unavailable_supervisor_persists_safe_fallback_and_reschedules(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-supervisor-unavailable"
    run_id = "00000000-0000-0000-0000-000000000308"
    FAIL_SUPERVISOR_FOR.add(order_id)
    handle = await _start_workflow(
        temporal_environment,
        order_id,
        run_id=run_id,
        provider="ollama",
    )
    try:
        snapshot = await _wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == 1
                and state.is_sleeping
                and any(
                    entry.entry_type == "supervisor_fallback" for entry in state.recent_timeline
                )
                and any(
                    transition.activity_type == "supervisor_fallback"
                    for transition in _transitions_for(order_id)
                )
            ),
        )

        assert SUPERVISOR_ATTEMPTS[order_id] == 3
        assert _action_requests_for(order_id) == []
        assert snapshot.memory_summary is None
        assert snapshot.next_wake_at is not None
        fallback = next(
            transition
            for transition in _transitions_for(order_id)
            if transition.activity_type == "supervisor_fallback"
        )
        assert fallback.status == "fallback"
        assert fallback.payload == {
            "trigger": "workflow_start",
            "provider": "ollama",
            "model": "qwen3:1.7b",
            "failure_type": "SupervisorProviderUnavailable",
        }
    finally:
        await _terminate(handle)


async def test_completion_recommendation_cannot_complete_workflow(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-completion-recommendation"
    run_id = "00000000-0000-0000-0000-000000000304"

    def recommendation_builder(request: SupervisorRequest) -> AgentDecisionData:
        decision = _default_decision(request)
        decision.completion_recommendation = True
        decision.decision_summary = "The AI recommends completion, without authorizing it."
        return decision

    DECISION_BUILDERS[order_id] = recommendation_builder
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    try:
        snapshot = await _wait_for_initial_sleep(handle)

        assert snapshot.completed is False
        assert snapshot.workflow_status is WorkflowStatus.SLEEPING
        assert snapshot.completion_reason is None
        assert snapshot.final_output is None
        assert _final_requests_for(order_id) == []
        decisions = [
            entry for entry in snapshot.recent_timeline if entry.entry_type == "supervisor_decision"
        ]
        assert decisions[-1].details["completion_recommendation"] is True
        assert not any(
            entry.entry_type in {"workflow_completed", "workflow_terminated"}
            for entry in snapshot.recent_timeline
        )
    finally:
        await _terminate(handle)


async def test_delivered_authorizes_final_output_before_terminal_transition(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-delivered-final-output"
    run_id = "00000000-0000-0000-0000-000000000305"
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    initial = await _wait_for_initial_sleep(handle)
    await handle.signal(
        OrderSupervisorWorkflow.receive_event,
        _event("stage3-delivered", EventType.DELIVERED),
    )

    with temporal_environment.auto_time_skipping_disabled():
        result = await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)

    assert result.workflow_status is WorkflowStatus.COMPLETED
    assert result.completed is True
    assert result.supervisor_invocation_count == initial.supervisor_invocation_count
    assert result.order_state.lifecycle == "completed"
    assert result.order_state.shipment == "delivered"
    assert result.final_output is not None
    assert result.final_output.final_summary == (f"Stub final output for {order_id} (completed).")

    final_requests = _final_requests_for(order_id)
    assert len(final_requests) == 1
    assert final_requests[0].completion_status is WorkflowStatus.COMPLETED
    assert final_requests[0].completion_reason == ("Order delivered (event_id=stage3-delivered).")
    assert final_requests[0].order_state.lifecycle == "completed"

    transitions = _transitions_for(order_id)
    activity_types = [transition.activity_type for transition in transitions]
    authorized_index = activity_types.index("workflow_completion_authorized")
    output_index = activity_types.index("final_output_generated")
    terminal_index = activity_types.index("workflow_completed")
    assert authorized_index < output_index < terminal_index
    assert transitions[terminal_index].workflow_status is WorkflowStatus.COMPLETED
    assert transitions[terminal_index].final_output is not None


async def test_graceful_termination_generates_final_output(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-termination-final-output"
    run_id = "00000000-0000-0000-0000-000000000306"
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    await _wait_for_initial_sleep(handle)

    result = await _terminate(handle, reason="Operator ended the Stage 3 run")

    assert result.workflow_status is WorkflowStatus.TERMINATED
    assert result.completion_reason == ("Terminated by operator: Operator ended the Stage 3 run")
    assert result.final_output is not None
    assert result.final_output.final_summary == (f"Stub final output for {order_id} (terminated).")
    final_requests = _final_requests_for(order_id)
    assert len(final_requests) == 1
    assert final_requests[0].completion_status is WorkflowStatus.TERMINATED
    assert final_requests[0].completion_reason == result.completion_reason

    activity_types = [transition.activity_type for transition in _transitions_for(order_id)]
    assert (
        activity_types.index("workflow_termination_authorized")
        < activity_types.index("final_output_generated")
        < activity_types.index("workflow_terminated")
    )


async def test_failed_final_output_uses_persisted_deterministic_fallback(
    temporal_environment: WorkflowEnvironment,
) -> None:
    order_id = "stage3-final-output-fallback"
    run_id = "00000000-0000-0000-0000-000000000307"
    FAIL_FINAL_OUTPUT_FOR.add(order_id)
    handle = await _start_workflow(temporal_environment, order_id, run_id=run_id)
    await _wait_for_initial_sleep(handle)
    await handle.signal(
        OrderSupervisorWorkflow.receive_event,
        _event("stage3-fallback-delivered", EventType.DELIVERED),
    )

    result = await asyncio.wait_for(handle.result(), timeout=QUERY_TIMEOUT_SECONDS)

    assert FINAL_OUTPUT_ATTEMPTS[order_id] == 3
    assert result.workflow_status is WorkflowStatus.COMPLETED
    assert result.final_output is not None
    assert result.final_output.final_summary == (
        f"Order {order_id} finished with status completed. "
        "Order delivered (event_id=stage3-fallback-delivered)."
    )
    assert result.final_output.important_actions
    assert result.final_output.key_learnings
    assert result.final_output.recommendations

    transitions = _transitions_for(order_id)
    fallback = next(
        transition
        for transition in transitions
        if transition.activity_type == "final_output_fallback"
    )
    terminal = next(
        transition for transition in transitions if transition.activity_type == "workflow_completed"
    )
    assert fallback.status == "fallback"
    assert fallback.payload["provider"] == "deterministic_fallback"
    assert fallback.final_output == terminal.final_output
    assert fallback.final_output == {
        "final_summary": result.final_output.final_summary,
        "important_actions": result.final_output.important_actions,
        "key_learnings": result.final_output.key_learnings,
        "recommendations": result.final_output.recommendations,
    }
    assert not any(
        transition.activity_type == "final_output_generated" for transition in transitions
    )
