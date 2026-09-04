"""Durable Temporal workflow supervising one order."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from app.temporal.models import (
    BUSINESS_ACTION_NAMES,
    AgentDecisionData,
    BusinessActionExecutionRequest,
    BusinessActionExecutionResult,
    CompactMemoryData,
    EventType,
    FinalOutputActivityResult,
    FinalOutputData,
    FinalOutputRequest,
    InterruptRequest,
    OrderEvent,
    OrderState,
    PauseRequest,
    PersistenceTransitionRequest,
    ResumeRequest,
    RunInstruction,
    SupervisorRequest,
    TerminateRequest,
    TimelineEntry,
    WorkflowInput,
    WorkflowSnapshot,
    WorkflowStatus,
    workflow_id_for_order,
)
from app.temporal.wake_policy import WakePolicyOutcome, classify_event

FAKE_SUPERVISOR_ACTIVITY_NAME = "fake_supervisor"
FINAL_OUTPUT_ACTIVITY_NAME = "generate_final_output"
EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME = "execute_business_action"
PERSIST_TRANSITION_ACTIVITY_NAME = "persist_workflow_transition"
DEFAULT_WAKE_SECONDS = 12
MIN_WAKE_SECONDS = 1
MAX_WAKE_SECONDS = 24 * 60 * 60
PERSISTENCE_RECOVERY_DELAY_SECONDS = 5
RECENT_TIMELINE_LIMIT = 50
TIMELINE_STATE_LIMIT = 200
SUPERVISOR_CONTEXT_ACTIVITY_LIMIT = 10
SUPERVISOR_CONTEXT_INSTRUCTION_LIMIT = 8
FINAL_CONTEXT_ACTIVITY_LIMIT = 25
TERMINAL_STATUSES = frozenset({WorkflowStatus.COMPLETED, WorkflowStatus.TERMINATED})
SEMANTIC_ACTION_GUARD_PATCH = "semantic-action-guard-v1"


@workflow.defn
class OrderSupervisorWorkflow:
    """Own the deterministic lifecycle and durable state for one order."""

    @workflow.init
    def __init__(self, workflow_input: WorkflowInput) -> None:
        self._order = workflow_input.order
        self._supervisor_instructions = workflow_input.supervisor_instructions
        self._run_id = workflow_input.run_id
        self._workflow_id = workflow_id_for_order(workflow_input.order.order_id)
        self._temporal_run_id: str | None = None
        self._available_actions = list(workflow_input.available_actions)
        self._supervisor_provider = self._provider_name(workflow_input.supervisor_provider)
        self._supervisor_model = workflow_input.supervisor_model
        self._wake_aggressiveness = workflow_input.wake_aggressiveness
        self._additional_instructions: list[RunInstruction] = []
        self._order_state = OrderState(attributes=dict(workflow_input.order.initial_state))
        self._status = WorkflowStatus.STARTING
        self._pending_events: list[OrderEvent] = []
        self._seen_event_ids: set[str] = set()
        self._timeline: list[TimelineEntry] = []
        self._timeline_sequence = 0
        self._pending_transitions: list[PersistenceTransitionRequest] = []
        self._supervisor_invocation_count = 0
        self._next_wake_at: datetime | None = None
        self._wake_interval_seconds = self._safe_wake_seconds(
            workflow_input.demo_wake_interval_seconds
        )
        self._scheduled_wake_overdue = False
        self._paused = False
        self._interrupted = False
        self._pause_reason: str | None = None
        self._interrupt_reason: str | None = None
        self._resume_pending = False
        self._termination_requested = False
        self._termination_reason: str | None = None
        self._signal_wakeup = False
        self._completion_reason: str | None = None
        self._completed_at: str | None = None
        self._memory_summary: CompactMemoryData | None = None
        self._final_output: FinalOutputData | None = None
        self._finalization_in_progress = False
        self._semantic_action_guard_enabled = False
        self._action_cause_event_id: str | None = None
        self._action_cause_event_type: EventType | None = None
        self._successful_action_keys_for_cause: dict[str, str] = {}

    @workflow.run
    async def run(self, workflow_input: WorkflowInput) -> WorkflowSnapshot:
        """Start supervision, then alternate between Signals and durable timers."""

        if not workflow_input.order.order_id.strip():
            raise ApplicationError(
                "order_id must not be empty",
                type="InvalidWorkflowInput",
                non_retryable=True,
            )

        info = workflow.info()
        self._workflow_id = info.workflow_id
        self._temporal_run_id = info.run_id
        self._semantic_action_guard_enabled = workflow.patched(SEMANTIC_ACTION_GUARD_PATCH)
        self._status = WorkflowStatus.RUNNING
        self._record_timeline(
            "workflow_started",
            f"Started supervision for order {self._order.order_id}.",
            {"supervisor_instructions": self._supervisor_instructions},
        )
        await self._invoke_supervisor(trigger="workflow_start")

        while self._status not in TERMINAL_STATUSES:
            await self._flush_pending_transitions()
            self._signal_wakeup = False

            if self._termination_requested:
                await self._finish_termination()
                break

            if self._is_blocked():
                await self._wait_while_blocked()
                continue

            if self._resume_pending:
                self._resume_pending = False
                if self._scheduled_wake_overdue or self._wake_deadline_has_passed():
                    self._scheduled_wake_overdue = False
                    await self._invoke_supervisor(
                        trigger="scheduled_wake",
                        scheduled_catch_up=True,
                    )
                    continue

            if self._pending_events:
                await self._process_pending_events()
                continue

            if self._next_wake_at is None:
                self._schedule_next_wake(self._wake_interval_seconds)

            remaining = self._next_wake_at - workflow.now()
            if remaining.total_seconds() <= 0:
                await self._invoke_supervisor(trigger="scheduled_wake")
                continue

            if self._status is not WorkflowStatus.SLEEPING:
                self._status = WorkflowStatus.SLEEPING
                self._record_timeline(
                    "workflow_sleeping",
                    "Workflow returned to durable sleep until the next trigger.",
                    {
                        "next_wake_at": self._next_wake_at.isoformat(),
                    },
                )
            try:
                await workflow.wait_condition(
                    self._normal_wait_condition,
                    timeout=remaining,
                    timeout_summary="next supervisor review",
                )
            except TimeoutError:
                if not self._normal_wait_condition():
                    await self._invoke_supervisor(trigger="scheduled_wake")

        await self._flush_pending_transitions()
        return self._snapshot()

    @workflow.signal
    def receive_event(self, event: OrderEvent) -> None:
        """Accept a unique event and let the main run loop process it."""

        if self._signals_are_closed():
            return

        event_type_value = self._event_type_value(event.event_type)
        if event.event_id in self._seen_event_ids:
            self._record_timeline(
                "duplicate_event_ignored",
                f"Ignored duplicate event {event.event_id}.",
                {"event_id": event.event_id, "event_type": event_type_value},
            )
            # Stage 1 direct-start histories did not wake for a duplicate Signal.
            # Persistence-enabled runs must wake so the duplicate audit row is flushed.
            self._signal_wakeup = self._run_id is not None
            return

        self._seen_event_ids.add(event.event_id)
        self._pending_events.append(event)
        self._record_timeline(
            "order_event_received",
            f"Accepted {event_type_value} event {event.event_id}.",
            {
                "event_id": event.event_id,
                "event_type": event_type_value,
                "occurred_at": event.occurred_at,
                "payload": dict(event.payload),
            },
        )
        self._signal_wakeup = True

    @workflow.signal
    def add_instruction(self, instruction: RunInstruction) -> None:
        """Retain a run-specific instruction for every future supervisor review."""

        if self._signals_are_closed():
            return

        retained = RunInstruction(
            instruction_id=instruction.instruction_id,
            instruction=instruction.instruction,
            created_at=instruction.created_at,
        )
        self._additional_instructions.append(retained)
        self._record_timeline(
            "instruction_added",
            "Added an operator instruction to the running supervisor.",
            {
                "instruction_id": retained.instruction_id,
                "instruction": retained.instruction,
                "created_at": retained.created_at,
            },
        )
        self._signal_wakeup = True

    @workflow.signal
    def pause(self, request: PauseRequest) -> None:
        """Pause automated processing while continuing to accept Signals."""

        if self._signals_are_closed():
            return
        self._paused = True
        self._interrupted = False
        self._pause_reason = request.reason
        self._interrupt_reason = None
        self._status = WorkflowStatus.PAUSED
        self._record_timeline(
            "workflow_paused",
            "Paused automated supervisor processing.",
            {"reason": request.reason},
        )
        self._signal_wakeup = True

    @workflow.signal
    def resume(self, request: ResumeRequest) -> None:
        """Resume a paused or interrupted workflow."""

        if not self._is_blocked() or self._signals_are_closed():
            return
        previous_status = self._status
        self._paused = False
        self._interrupted = False
        self._pause_reason = None
        self._interrupt_reason = None
        self._status = WorkflowStatus.RUNNING
        self._resume_pending = True
        self._record_timeline(
            "workflow_resumed",
            "Resumed automated supervisor processing.",
            {"from_status": previous_status.value, "reason": request.reason},
        )
        self._signal_wakeup = True

    @workflow.signal
    def interrupt(self, request: InterruptRequest) -> None:
        """Enter a blocking human-review state until resume or termination."""

        if self._signals_are_closed():
            return
        self._paused = False
        self._interrupted = True
        self._pause_reason = None
        self._interrupt_reason = request.reason
        self._status = WorkflowStatus.INTERRUPTED
        self._record_timeline(
            "workflow_interrupted",
            "Interrupted automated processing for human review.",
            {"reason": request.reason},
        )
        self._signal_wakeup = True

    @workflow.signal
    def request_termination(self, request: TerminateRequest) -> None:
        """Request a graceful Workflow-owned terminal transition."""

        if self._signals_are_closed():
            return
        self._termination_requested = True
        self._termination_reason = request.reason
        self._record_timeline(
            "termination_requested",
            "Received a graceful termination request.",
            {"reason": request.reason},
        )
        self._signal_wakeup = True

    @workflow.query
    def get_state(self) -> WorkflowSnapshot:
        """Return a read-only snapshot for clients, tests, and demonstrations."""

        return self._snapshot()

    async def _process_pending_events(self) -> None:
        while (
            self._pending_events
            and self._status not in TERMINAL_STATUSES
            and not self._is_blocked()
            and not self._termination_requested
        ):
            event = self._pending_events.pop(0)
            self._apply_event_to_order_state(event)

            outcome = classify_event(event.event_type)
            if outcome is WakePolicyOutcome.COMPLETE:
                await self._finish_delivery(event)
                return
            if outcome is WakePolicyOutcome.SUPPRESS:
                self._record_timeline(
                    "supervisor_wake_suppressed",
                    f"Suppressed supervisor wake for routine {event.event_type.value} event.",
                    {"event_id": event.event_id, "event_type": event.event_type.value},
                )
                continue

            self._record_timeline(
                "supervisor_wake_requested",
                f"Important {event.event_type.value} event woke the supervisor.",
                {"event_id": event.event_id, "event_type": event.event_type.value},
            )
            await self._invoke_supervisor(
                trigger=f"event:{event.event_type.value}",
                event_type=event.event_type,
                event_id=event.event_id,
            )

    async def _invoke_supervisor(
        self,
        *,
        trigger: str,
        event_type: EventType | None = None,
        event_id: str | None = None,
        scheduled_catch_up: bool = False,
    ) -> None:
        if self._is_blocked() or self._termination_requested:
            return

        if event_id is not None and event_type is not None:
            self._action_cause_event_id = event_id
            self._action_cause_event_type = event_type
            self._successful_action_keys_for_cause.clear()

        self._status = WorkflowStatus.RUNNING
        self._next_wake_at = None
        self._scheduled_wake_overdue = False
        if trigger == "scheduled_wake":
            self._record_timeline(
                "scheduled_wake",
                "The scheduled supervisor review became due.",
                {"catch_up_after_block": scheduled_catch_up},
            )

        self._supervisor_invocation_count += 1
        self._record_timeline(
            "supervisor_invocation",
            f"Invoked the {self._supervisor_provider} supervisor for {trigger}.",
            {
                "trigger": trigger,
                "event_type": event_type.value if event_type else None,
                "provider": self._supervisor_provider,
                "model": self._supervisor_model,
                "context_instruction_ids": [
                    instruction.instruction_id
                    for instruction in self._additional_instructions[
                        -SUPERVISOR_CONTEXT_INSTRUCTION_LIMIT:
                    ]
                ],
                "context_instruction_count": min(
                    len(self._additional_instructions),
                    SUPERVISOR_CONTEXT_INSTRUCTION_LIMIT,
                ),
            },
        )
        try:
            decision = await workflow.execute_activity(
                FAKE_SUPERVISOR_ACTIVITY_NAME,
                SupervisorRequest(
                    order=self._order,
                    supervisor_instructions=self._supervisor_instructions,
                    trigger=trigger,
                    event_type=event_type,
                    next_wake_seconds=self._wake_interval_seconds,
                    additional_instructions=self._copy_instructions(),
                    available_actions=list(self._available_actions),
                    order_state=self._copy_order_state(),
                    memory_summary=self._copy_memory(),
                    recent_activity=self._copy_recent_timeline(SUPERVISOR_CONTEXT_ACTIVITY_LIMIT),
                    provider=self._supervisor_provider,
                    model=self._supervisor_model,
                    wake_aggressiveness=self._wake_aggressiveness,
                ),
                result_type=AgentDecisionData,
                schedule_to_close_timeout=timedelta(seconds=110),
                start_to_close_timeout=timedelta(seconds=35),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2,
                    maximum_interval=timedelta(seconds=4),
                    maximum_attempts=3,
                ),
                activity_id=f"fake-supervisor-{self._supervisor_invocation_count}",
                summary=f"Supervisor review: {trigger}",
            )
        except ActivityError as error:
            failure_type = self._activity_error_type(error)
            invalid_result = failure_type in {
                "InvalidSupervisorDecision",
                "InvalidSupervisorProvider",
            }
            self._record_timeline(
                "supervisor_decision_rejected" if invalid_result else "supervisor_fallback",
                (
                    "The supervisor decision failed validation and no action was executed."
                    if invalid_result
                    else "The configured supervisor was unavailable; supervision will retry at "
                    "the next review."
                ),
                {
                    "trigger": trigger,
                    "provider": self._supervisor_provider,
                    "model": self._supervisor_model,
                    "failure_type": failure_type,
                },
                status="rejected" if invalid_result else "fallback",
            )
            if not self._termination_requested:
                self._schedule_next_wake(self._wake_interval_seconds)
            await self._flush_pending_transitions()
            return

        validation_outcomes = [
            {
                "action_name": proposal.action_name,
                "outcome": self._proposal_validation_outcome(proposal.action_name),
            }
            for proposal in decision.actions
        ]
        self._record_timeline(
            "supervisor_decision",
            decision.decision_summary,
            {
                "trigger": decision.trigger,
                "provider": decision.provider,
                "model": decision.model,
                "urgency": decision.urgency,
                "should_act": decision.should_act,
                "proposed_action_names": list(decision.proposed_action_names),
                "action_validation": validation_outcomes,
                "next_wake_seconds": decision.next_wake_seconds,
                "completion_recommendation": decision.completion_recommendation,
            },
            status="validated",
        )

        executed_actions = await self._execute_proposed_actions(decision, trigger=trigger)
        self._memory_summary = self._validated_memory_update(
            decision.memory_update,
            executed_actions=executed_actions,
        )
        self._record_timeline(
            "memory_updated",
            "Updated the compact rolling memory after supervisor review.",
            {"memory_summary": self._memory_payload()},
            status="updated",
        )
        if not self._termination_requested:
            safe_wake = self._safe_wake_seconds(decision.next_wake_seconds)
            if safe_wake != decision.next_wake_seconds:
                self._record_timeline(
                    "supervisor_wake_adjusted",
                    "Adjusted an invalid supervisor wake interval to the safe default.",
                    {
                        "requested_seconds": decision.next_wake_seconds,
                        "scheduled_seconds": safe_wake,
                    },
                    status="adjusted",
                )
            self._schedule_next_wake(decision.next_wake_seconds)
        await self._flush_pending_transitions()

    async def _execute_proposed_actions(
        self,
        decision: AgentDecisionData,
        *,
        trigger: str,
    ) -> list[str]:
        executed: list[str] = []
        for action_index, proposal in enumerate(decision.actions):
            validation_outcome = self._proposal_validation_outcome(proposal.action_name)
            if validation_outcome != "accepted":
                entry_type = (
                    "business_action_skipped"
                    if validation_outcome == "persistence_unavailable"
                    else "business_action_rejected"
                )
                self._record_timeline(
                    entry_type,
                    self._action_rejection_summary(proposal.action_name, validation_outcome),
                    {
                        "action_name": proposal.action_name,
                        "validation_outcome": validation_outcome,
                        "trigger": trigger,
                    },
                    status="rejected",
                )
                continue

            if self._is_blocked() or self._termination_requested:
                self._record_timeline(
                    "business_action_rejected",
                    f"Did not execute {proposal.action_name} because automation became blocked.",
                    {
                        "action_name": proposal.action_name,
                        "validation_outcome": "workflow_blocked",
                        "trigger": trigger,
                    },
                    status="rejected",
                )
                continue

            idempotency_key = (
                f"{self._workflow_id}:{self._run_id}:action:"
                f"{self._supervisor_invocation_count:08d}:{action_index:04d}"
            )
            original_idempotency_key = self._successful_action_keys_for_cause.get(
                proposal.action_name
            )
            if self._semantic_action_guard_enabled and original_idempotency_key is not None:
                self._record_timeline(
                    "business_action_suppressed",
                    (
                        f"Suppressed duplicate action {proposal.action_name}; it already "
                        "executed for the current causal event."
                    ),
                    {
                        "action_name": proposal.action_name,
                        "validation_outcome": "semantic_duplicate",
                        "reason": "same_action_same_causal_event",
                        "event_id": self._action_cause_event_id,
                        "event_type": (
                            self._action_cause_event_type.value
                            if self._action_cause_event_type is not None
                            else None
                        ),
                        "trigger": trigger,
                        "supervisor_invocation": self._supervisor_invocation_count,
                        "action_index": action_index,
                        "semantic_action_key": self._semantic_action_key(proposal.action_name),
                        "original_idempotency_key": original_idempotency_key,
                        "candidate_idempotency_key": idempotency_key,
                    },
                    status="suppressed",
                )
                continue

            try:
                result = await workflow.execute_activity(
                    EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME,
                    BusinessActionExecutionRequest(
                        run_id=self._run_id or "",
                        workflow_id=self._workflow_id,
                        idempotency_key=idempotency_key,
                        requested_at=workflow.now().isoformat(),
                        trigger=trigger,
                        supervisor_invocation=self._supervisor_invocation_count,
                        action_index=action_index,
                        action_name=proposal.action_name,
                        arguments=dict(proposal.arguments),
                        available_actions=list(self._available_actions),
                    ),
                    result_type=BusinessActionExecutionResult,
                    schedule_to_close_timeout=timedelta(seconds=45),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2,
                        maximum_interval=timedelta(seconds=4),
                        maximum_attempts=3,
                    ),
                    activity_id=(
                        f"business-action-{self._supervisor_invocation_count}-{action_index}"
                    ),
                    summary=f"Simulate business action: {proposal.action_name}",
                )
            except ActivityError as error:
                failure_type = self._activity_error_type(error)
                rejected = failure_type in {
                    "InvalidBusinessAction",
                    "DisabledBusinessAction",
                    "InvalidBusinessActionAllowlist",
                    "BusinessActionIdempotencyConflict",
                }
                self._record_timeline(
                    "business_action_rejected" if rejected else "business_action_failed",
                    (
                        f"Rejected proposed action {proposal.action_name}."
                        if rejected
                        else f"Could not persist simulated action {proposal.action_name}."
                    ),
                    {
                        "action_name": proposal.action_name,
                        "validation_outcome": "rejected" if rejected else "failed",
                        "failure_type": failure_type,
                        "trigger": trigger,
                    },
                    status="rejected" if rejected else "failed",
                )
                continue

            if self._semantic_action_guard_enabled:
                self._successful_action_keys_for_cause.setdefault(
                    result.action_name,
                    result.idempotency_key,
                )
            executed.append(result.action_name)
            self._record_timeline(
                "business_action_executed",
                result.summary,
                {
                    "action_name": result.action_name,
                    "idempotency_key": result.idempotency_key,
                    "status": result.status,
                    "persisted": result.persisted,
                    "trigger": trigger,
                },
                persist=False,
                status="executed",
            )
        return executed

    async def _wait_while_blocked(self) -> None:
        if self._next_wake_at is not None and not self._scheduled_wake_overdue:
            remaining = self._next_wake_at - workflow.now()
            if remaining.total_seconds() <= 0:
                self._mark_scheduled_wake_deferred()
                await self._flush_pending_transitions()
                return
            try:
                await workflow.wait_condition(
                    self._blocked_wait_condition,
                    timeout=remaining,
                    timeout_summary="blocked until next supervisor review deadline",
                )
            except TimeoutError:
                if self._is_blocked() and not self._termination_requested:
                    self._mark_scheduled_wake_deferred()
                    await self._flush_pending_transitions()
            return

        await workflow.wait_condition(self._blocked_wait_condition)

    async def _finish_termination(self) -> None:
        ignored_event_count = len(self._pending_events)
        self._pending_events.clear()
        reason = self._termination_reason or "No termination reason supplied."
        await self._finish_terminal(
            status=WorkflowStatus.TERMINATED,
            completion_reason=f"Terminated by operator: {reason}",
            authorization_type="workflow_termination_authorized",
            terminal_entry_type="workflow_terminated",
            details={
                "reason": reason,
                "rule": "graceful_termination_signal",
                "events_ignored_after_terminal": ignored_event_count,
            },
        )

    async def _finish_delivery(self, event: OrderEvent) -> None:
        ignored_event_count = len(self._pending_events)
        self._pending_events.clear()
        await self._finish_terminal(
            status=WorkflowStatus.COMPLETED,
            completion_reason=f"Order delivered (event_id={event.event_id}).",
            authorization_type="workflow_completion_authorized",
            terminal_entry_type="workflow_completed",
            details={
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "rule": "terminal_delivered_event",
                "events_ignored_after_terminal": ignored_event_count,
            },
        )

    async def _finish_terminal(
        self,
        *,
        status: WorkflowStatus,
        completion_reason: str,
        authorization_type: str,
        terminal_entry_type: str,
        details: dict[str, Any],
    ) -> None:
        """Authorize deterministically, generate a report, then enter terminal state."""

        self._finalization_in_progress = True
        self._status = WorkflowStatus.RUNNING
        self._paused = False
        self._interrupted = False
        self._pause_reason = None
        self._interrupt_reason = None
        self._next_wake_at = None
        self._scheduled_wake_overdue = False
        self._completion_reason = completion_reason
        self._completed_at = workflow.now().isoformat()
        self._record_timeline(
            authorization_type,
            f"Workflow lifecycle rules authorized {status.value} finalization.",
            {**details, "target_status": status.value},
            status="authorized",
        )
        await self._flush_pending_transitions()

        try:
            result = await workflow.execute_activity(
                FINAL_OUTPUT_ACTIVITY_NAME,
                FinalOutputRequest(
                    order=self._order,
                    order_state=self._copy_order_state(),
                    supervisor_instructions=self._supervisor_instructions,
                    completion_status=status,
                    completion_reason=completion_reason,
                    memory_summary=self._copy_memory(),
                    recent_activity=self._copy_recent_timeline(FINAL_CONTEXT_ACTIVITY_LIMIT),
                    additional_instructions=self._copy_instructions(),
                    provider=self._supervisor_provider,
                    model=self._supervisor_model,
                ),
                result_type=FinalOutputActivityResult,
                schedule_to_close_timeout=timedelta(seconds=110),
                start_to_close_timeout=timedelta(seconds=35),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2,
                    maximum_interval=timedelta(seconds=4),
                    maximum_attempts=3,
                ),
                activity_id="final-output",
                summary=f"Generate final output for {status.value} run",
            )
        except ActivityError as error:
            self._final_output = self._deterministic_final_output(status, completion_reason)
            self._record_timeline(
                "final_output_fallback",
                "Generated deterministic final output after the configured provider failed.",
                {
                    "provider": "deterministic_fallback",
                    "configured_provider": self._supervisor_provider,
                    "failure_type": self._activity_error_type(error),
                    "final_output": self._final_output_payload(),
                },
                status="fallback",
            )
        else:
            self._final_output = self._copy_final_output(result.output)
            self._record_timeline(
                "final_output_generated",
                "Generated and validated the final supervisor output.",
                {
                    "provider": result.provider,
                    "model": result.model,
                    "final_output": self._final_output_payload(),
                },
                status="generated",
            )

        self._status = status
        self._record_timeline(
            terminal_entry_type,
            completion_reason,
            details,
            status=status.value,
        )
        await self._flush_pending_transitions()
        self._finalization_in_progress = False

    async def _flush_pending_transitions(self) -> None:
        while self._pending_transitions:
            transition = self._pending_transitions[0]
            recovery_cycle = 1
            while True:
                try:
                    await workflow.execute_activity(
                        PERSIST_TRANSITION_ACTIVITY_NAME,
                        transition,
                        schedule_to_close_timeout=timedelta(seconds=30),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                        activity_id=(
                            f"persist-transition-{transition.sequence}-cycle-{recovery_cycle}"
                        ),
                        summary=f"Persist Workflow transition: {transition.activity_type}",
                    )
                except ActivityError as error:
                    if isinstance(error.cause, ApplicationError) and error.cause.non_retryable:
                        raise
                    self._record_timeline(
                        "persistence_fallback",
                        "A Workflow transition exhausted bounded Activity retries; "
                        "a durable recovery retry is scheduled.",
                        {
                            "activity_key": transition.activity_key,
                            "activity_type": transition.activity_type,
                            "recovery_cycle": recovery_cycle,
                        },
                        persist=False,
                    )
                    recovery_cycle += 1
                    await workflow.sleep(timedelta(seconds=PERSISTENCE_RECOVERY_DELAY_SECONDS))
                    continue

                self._pending_transitions.pop(0)
                break

    def _schedule_next_wake(self, seconds: int) -> None:
        safe_seconds = self._safe_wake_seconds(seconds)
        self._next_wake_at = workflow.now() + timedelta(seconds=safe_seconds)
        self._scheduled_wake_overdue = False
        if not self._is_blocked():
            self._status = WorkflowStatus.SLEEPING
        self._record_timeline(
            "sleep_scheduled",
            f"Scheduled the next supervisor review in {safe_seconds} seconds.",
            {"next_wake_at": self._next_wake_at.isoformat(), "wake_seconds": safe_seconds},
        )

    def _mark_scheduled_wake_deferred(self) -> None:
        if self._scheduled_wake_overdue:
            return
        self._scheduled_wake_overdue = True
        self._record_timeline(
            "scheduled_wake_deferred",
            "Deferred the scheduled supervisor review while automated processing is blocked.",
            {
                "blocked_status": self._status.value,
                "next_wake_at": self._next_wake_at.isoformat() if self._next_wake_at else None,
            },
        )

    def _apply_event_to_order_state(self, event: OrderEvent) -> None:
        self._order_state.last_event_type = event.event_type
        self._order_state.event_count += 1
        self._order_state.attributes["last_event_payload"] = dict(event.payload)

        if event.event_type is EventType.ORDER_CREATED:
            self._order_state.lifecycle = "open"
        elif event.event_type is EventType.PAYMENT_CONFIRMED:
            self._order_state.payment = "confirmed"
        elif event.event_type is EventType.PAYMENT_FAILED:
            self._order_state.payment = "failed"
        elif event.event_type is EventType.SHIPMENT_CREATED:
            self._order_state.shipment = "created"
        elif event.event_type is EventType.SHIPMENT_DELAYED:
            self._order_state.shipment = "delayed"
        elif event.event_type is EventType.DELIVERED:
            self._order_state.shipment = "delivered"
            self._order_state.lifecycle = "completed"
        elif event.event_type is EventType.REFUND_REQUESTED:
            self._order_state.refund = "requested"

    def _record_timeline(
        self,
        entry_type: str,
        summary: str,
        details: dict[str, Any] | None = None,
        *,
        persist: bool = True,
        status: str = "recorded",
    ) -> None:
        self._timeline_sequence += 1
        recorded_at = workflow.now().isoformat()
        entry_details = details or {}
        self._timeline.append(
            TimelineEntry(
                sequence=self._timeline_sequence,
                recorded_at=recorded_at,
                entry_type=entry_type,
                summary=summary,
                details=entry_details,
            )
        )
        if len(self._timeline) > TIMELINE_STATE_LIMIT:
            del self._timeline[:-TIMELINE_STATE_LIMIT]

        if self._run_id is not None and persist:
            self._pending_transitions.append(
                PersistenceTransitionRequest(
                    run_id=self._run_id,
                    workflow_id=self._workflow_id,
                    temporal_run_id=self._temporal_run_id,
                    activity_key=(
                        f"{self._workflow_id}:{self._run_id}:{self._timeline_sequence:08d}"
                    ),
                    sequence=self._timeline_sequence,
                    recorded_at=recorded_at,
                    activity_type=entry_type,
                    source=self._activity_source(entry_type),
                    status=status,
                    summary=summary,
                    payload=dict(entry_details),
                    event_name=self._optional_string(entry_details.get("event_type")),
                    action_name=self._optional_string(entry_details.get("action_name")),
                    external_event_id=self._optional_string(entry_details.get("event_id")),
                    workflow_status=self._status,
                    current_order_state=self._order_state_payload(),
                    additional_instructions=self._instruction_payloads(),
                    next_wake_at=self._next_wake_at.isoformat() if self._next_wake_at else None,
                    completion_reason=self._completion_reason,
                    completed_at=self._completed_at,
                    memory_summary=self._memory_payload(),
                    final_output=self._final_output_payload(),
                )
            )

    def _snapshot(self) -> WorkflowSnapshot:
        return WorkflowSnapshot(
            order_id=self._order.order_id,
            workflow_status=self._status,
            order_state=self._copy_order_state(),
            pending_event_count=len(self._pending_events),
            seen_event_count=len(self._seen_event_ids),
            is_sleeping=self._status is WorkflowStatus.SLEEPING,
            next_wake_at=self._next_wake_at.isoformat() if self._next_wake_at else None,
            supervisor_invocation_count=self._supervisor_invocation_count,
            recent_timeline=self._copy_recent_timeline(RECENT_TIMELINE_LIMIT),
            completed=self._status in TERMINAL_STATUSES,
            completion_reason=self._completion_reason,
            paused=self._paused,
            interrupted=self._interrupted,
            pause_reason=self._pause_reason,
            interrupt_reason=self._interrupt_reason,
            additional_instructions=self._copy_instructions(),
            memory_summary=self._copy_memory(),
            final_output=self._copy_final_output(self._final_output),
        )

    def _normal_wait_condition(self) -> bool:
        return bool(
            self._pending_events
            or self._signal_wakeup
            or self._pending_transitions
            or self._is_blocked()
            or self._termination_requested
        )

    def _blocked_wait_condition(self) -> bool:
        return bool(
            not self._is_blocked()
            or self._signal_wakeup
            or self._pending_transitions
            or self._termination_requested
        )

    def _is_blocked(self) -> bool:
        return self._paused or self._interrupted

    def _signals_are_closed(self) -> bool:
        return bool(
            self._status in TERMINAL_STATUSES
            or self._termination_requested
            or self._finalization_in_progress
        )

    def _wake_deadline_has_passed(self) -> bool:
        return self._next_wake_at is not None and self._next_wake_at <= workflow.now()

    def _copy_instructions(self) -> list[RunInstruction]:
        return [
            RunInstruction(
                instruction_id=instruction.instruction_id,
                instruction=instruction.instruction,
                created_at=instruction.created_at,
            )
            for instruction in self._additional_instructions
        ]

    def _copy_order_state(self) -> OrderState:
        return OrderState(
            lifecycle=self._order_state.lifecycle,
            payment=self._order_state.payment,
            shipment=self._order_state.shipment,
            refund=self._order_state.refund,
            last_event_type=self._order_state.last_event_type,
            event_count=self._order_state.event_count,
            attributes=dict(self._order_state.attributes),
        )

    def _copy_recent_timeline(self, limit: int) -> list[TimelineEntry]:
        return [
            TimelineEntry(
                sequence=entry.sequence,
                recorded_at=entry.recorded_at,
                entry_type=entry.entry_type,
                summary=entry.summary,
                details=dict(entry.details),
            )
            for entry in self._timeline[-limit:]
        ]

    def _copy_memory(self) -> CompactMemoryData | None:
        if self._memory_summary is None:
            return None
        memory = self._memory_summary
        return CompactMemoryData(
            order_state=memory.order_state,
            important_facts=list(memory.important_facts),
            open_issues=list(memory.open_issues),
            actions_taken=list(memory.actions_taken),
            active_constraints=list(memory.active_constraints),
            next_review=memory.next_review,
        )

    @staticmethod
    def _copy_final_output(output: FinalOutputData | None) -> FinalOutputData | None:
        if output is None:
            return None
        return FinalOutputData(
            final_summary=output.final_summary,
            important_actions=list(output.important_actions),
            key_learnings=list(output.key_learnings),
            recommendations=list(output.recommendations),
        )

    def _validated_memory_update(
        self,
        memory: CompactMemoryData,
        *,
        executed_actions: list[str],
    ) -> CompactMemoryData:
        # Action effects are Workflow/Activity-owned facts. Provider memory may summarize
        # context, but it cannot claim that a rejected or unexecuted action occurred.
        actions = (
            list(self._memory_summary.actions_taken) if self._memory_summary is not None else []
        )
        for action_name in executed_actions:
            if action_name not in actions:
                actions.append(action_name)
        return CompactMemoryData(
            order_state=self._bounded_text(memory.order_state, 500),
            important_facts=self._bounded_text_list(memory.important_facts),
            open_issues=self._bounded_text_list(memory.open_issues),
            actions_taken=self._bounded_text_list(actions),
            active_constraints=self._bounded_text_list(memory.active_constraints),
            next_review=self._bounded_text(memory.next_review, 300),
        )

    def _deterministic_final_output(
        self,
        status: WorkflowStatus,
        completion_reason: str,
    ) -> FinalOutputData:
        action_names = self._memory_summary.actions_taken if self._memory_summary else []
        important_actions = (
            [f"Executed simulated action {name}." for name in action_names]
            if action_names
            else ["No simulated business action was required before the run ended."]
        )
        return FinalOutputData(
            final_summary=(
                f"Order {self._order.order_id} finished with status {status.value}. "
                f"{completion_reason}"
            ),
            important_actions=important_actions,
            key_learnings=[
                f"The Workflow processed {self._order_state.event_count} unique order events.",
                "Temporal lifecycle rules authorized the terminal state.",
            ],
            recommendations=["Review the persistent timeline for operational follow-up."],
        )

    def _memory_payload(self) -> dict[str, Any] | None:
        memory = self._memory_summary
        if memory is None:
            return None
        return {
            "order_state": memory.order_state,
            "important_facts": list(memory.important_facts),
            "open_issues": list(memory.open_issues),
            "actions_taken": list(memory.actions_taken),
            "active_constraints": list(memory.active_constraints),
            "next_review": memory.next_review,
        }

    def _final_output_payload(self) -> dict[str, Any] | None:
        output = self._final_output
        if output is None:
            return None
        return {
            "final_summary": output.final_summary,
            "important_actions": list(output.important_actions),
            "key_learnings": list(output.key_learnings),
            "recommendations": list(output.recommendations),
        }

    def _proposal_validation_outcome(self, action_name: str) -> str:
        if action_name not in BUSINESS_ACTION_NAMES:
            return "unknown_action"
        if action_name not in self._available_actions:
            return "disabled_action"
        if self._run_id is None:
            return "persistence_unavailable"
        return "accepted"

    def _semantic_action_key(self, action_name: str) -> str:
        cause = (
            f"event:{self._action_cause_event_id}"
            if self._action_cause_event_id is not None
            else "workflow_start"
        )
        return f"{action_name}:{cause}"

    @staticmethod
    def _action_rejection_summary(action_name: str, outcome: str) -> str:
        if outcome == "persistence_unavailable":
            return (
                f"Skipped executable action {action_name} on the legacy direct Workflow path "
                "because no persistent run record exists."
            )
        if outcome == "disabled_action":
            return f"Rejected disabled action {action_name}."
        return "Rejected an unknown proposed business action."

    @staticmethod
    def _activity_error_type(error: ActivityError) -> str:
        if isinstance(error.cause, ApplicationError):
            return error.cause.type or "ApplicationError"
        return "ActivityFailure"

    @staticmethod
    def _bounded_text(value: str, limit: int) -> str:
        stripped = value.strip()
        return stripped if len(stripped) <= limit else stripped[:limit]

    @classmethod
    def _bounded_text_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            bounded = cls._bounded_text(str(value), 300)
            if bounded and bounded not in normalized:
                normalized.append(bounded)
        return normalized[-6:]

    def _instruction_payloads(self) -> list[dict[str, str]]:
        return [
            {
                "instruction_id": instruction.instruction_id,
                "instruction": instruction.instruction,
                "created_at": instruction.created_at,
            }
            for instruction in self._additional_instructions
        ]

    def _order_state_payload(self) -> dict[str, Any]:
        return {
            "lifecycle": self._order_state.lifecycle,
            "payment": self._order_state.payment,
            "shipment": self._order_state.shipment,
            "refund": self._order_state.refund,
            "last_event_type": self._order_state.last_event_type.value
            if self._order_state.last_event_type
            else None,
            "event_count": self._order_state.event_count,
            "attributes": dict(self._order_state.attributes),
        }

    @staticmethod
    def _activity_source(entry_type: str) -> str:
        if entry_type in {"order_event_received", "duplicate_event_ignored"}:
            return "order_event"
        if entry_type in {
            "instruction_added",
            "workflow_paused",
            "workflow_resumed",
            "workflow_interrupted",
            "termination_requested",
        }:
            return "operator"
        if entry_type.startswith("supervisor_"):
            return "supervisor"
        if entry_type.startswith("business_action"):
            return "business_action"
        if entry_type.startswith("memory_") or entry_type.startswith("final_output"):
            return "supervisor"
        if entry_type in {
            "scheduled_wake",
            "scheduled_wake_deferred",
            "sleep_scheduled",
            "workflow_sleeping",
        }:
            return "temporal"
        return "workflow"

    @staticmethod
    def _event_type_value(event_type: EventType | str) -> str:
        return event_type.value if isinstance(event_type, EventType) else str(event_type)

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _safe_wake_seconds(seconds: int | None) -> int:
        if seconds is not None and MIN_WAKE_SECONDS <= seconds <= MAX_WAKE_SECONDS:
            return seconds
        return DEFAULT_WAKE_SECONDS

    @staticmethod
    def _provider_name(provider: str | None) -> str:
        candidate = (provider or "deterministic").strip().lower()
        return "deterministic" if candidate in {"", "none"} else candidate
