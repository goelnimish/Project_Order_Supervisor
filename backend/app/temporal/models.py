"""Serialization-safe domain models shared by Temporal clients and workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BusinessActionName(StrEnum):
    """Exact simulated business actions available to every supervisor provider."""

    MESSAGE_FULFILLMENT_TEAM = "message_fulfillment_team"
    MESSAGE_PAYMENTS_TEAM = "message_payments_team"
    MESSAGE_LOGISTICS_TEAM = "message_logistics_team"
    MESSAGE_CUSTOMER = "message_customer"
    CREATE_INTERNAL_NOTE = "create_internal_note"


BUSINESS_ACTION_NAMES = tuple(action.value for action in BusinessActionName)


class EventType(StrEnum):
    """Order events accepted by the Stage 1 workflow."""

    ORDER_CREATED = "order_created"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PAYMENT_FAILED = "payment_failed"
    SHIPMENT_CREATED = "shipment_created"
    SHIPMENT_DELAYED = "shipment_delayed"
    DELIVERED = "delivered"
    REFUND_REQUESTED = "refund_requested"
    CUSTOMER_MESSAGE_RECEIVED = "customer_message_received"
    NO_UPDATE_FOR_N_HOURS = "no_update_for_n_hours"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> EventType | None:
        """Safely route novel serialized event names through the unknown policy."""

        if isinstance(value, str):
            return cls.UNKNOWN
        return None


class WorkflowStatus(StrEnum):
    """Observable lifecycle states owned by the workflow."""

    STARTING = "starting"
    RUNNING = "running"
    SLEEPING = "sleeping"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


@dataclass
class OrderContext:
    """Stable order context supplied when starting a workflow."""

    order_id: str
    customer_id: str | None = None
    initial_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderEvent:
    """An idempotently processed event sent through the workflow Signal."""

    event_id: str
    event_type: EventType
    occurred_at: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowInput:
    """Input used to initialize one order's workflow."""

    order: OrderContext
    supervisor_instructions: str
    demo_wake_interval_seconds: int | None = 12
    run_id: str | None = None
    available_actions: list[str] = field(default_factory=lambda: list(BUSINESS_ACTION_NAMES))
    supervisor_provider: str = "deterministic"
    supervisor_model: str | None = None
    wake_aggressiveness: str = "moderate"


@dataclass
class OrderState:
    """Small workflow-owned projection of the latest order state."""

    lifecycle: str = "open"
    payment: str = "pending"
    shipment: str = "not_created"
    refund: str = "not_requested"
    last_event_type: EventType | None = None
    event_count: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimelineEntry:
    """A concise, user-appropriate audit entry in workflow state."""

    sequence: int
    recorded_at: str
    entry_type: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunInstruction:
    """A typed operator instruction retained by an already-running workflow."""

    instruction_id: str
    instruction: str
    created_at: str


@dataclass
class PauseRequest:
    """Request that automated processing pause while Signals remain accepted."""

    reason: str | None = None


@dataclass
class ResumeRequest:
    """Request that a paused or interrupted workflow resume processing."""

    reason: str | None = None


@dataclass
class InterruptRequest:
    """Request urgent human review before automated processing continues."""

    reason: str


@dataclass
class TerminateRequest:
    """Request deterministic, graceful workflow termination."""

    reason: str


@dataclass
class SupervisorRequest:
    """Bounded, serialization-safe input to the supervisor Activity."""

    order: OrderContext
    supervisor_instructions: str
    trigger: str
    event_type: EventType | None
    next_wake_seconds: int
    additional_instructions: list[RunInstruction] = field(default_factory=list)
    available_actions: list[str] = field(default_factory=lambda: list(BUSINESS_ACTION_NAMES))
    order_state: OrderState = field(default_factory=OrderState)
    memory_summary: CompactMemoryData | None = None
    recent_activity: list[TimelineEntry] = field(default_factory=list)
    provider: str = "deterministic"
    model: str | None = None
    wake_aggressiveness: str = "moderate"


@dataclass
class BusinessActionProposalData:
    """Validated action proposal carried through Temporal history."""

    action_name: str
    arguments: dict[str, Any]


@dataclass
class CompactMemoryData:
    """Small rolling semantic memory owned by the Workflow."""

    order_state: str = ""
    important_facts: list[str] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    active_constraints: list[str] = field(default_factory=list)
    next_review: str = ""


@dataclass
class AgentDecisionData:
    """Validated supervisor decision carried through Temporal history."""

    trigger: str
    decision_summary: str
    should_act: bool
    actions: list[BusinessActionProposalData]
    memory_update: CompactMemoryData
    next_wake_seconds: int
    completion_recommendation: bool
    urgency: str
    provider: str
    model: str

    @property
    def proposed_action_names(self) -> list[str]:
        """Preserve the Stage 1/2 read shape while actions become typed."""

        return [action.action_name for action in self.actions]


# Compatibility imports retained for the frozen Stage 1/2 test and demo surface.
FakeSupervisorRequest = SupervisorRequest
FakeSupervisorDecision = AgentDecisionData


@dataclass
class FinalOutputData:
    """Typed end-of-run report generated only after lifecycle authorization."""

    final_summary: str
    important_actions: list[str]
    key_learnings: list[str]
    recommendations: list[str]


@dataclass
class FinalOutputRequest:
    """Bounded context for the final-output Activity."""

    order: OrderContext
    order_state: OrderState
    supervisor_instructions: str
    completion_status: WorkflowStatus
    completion_reason: str
    memory_summary: CompactMemoryData | None = None
    recent_activity: list[TimelineEntry] = field(default_factory=list)
    additional_instructions: list[RunInstruction] = field(default_factory=list)
    provider: str = "deterministic"
    model: str | None = None


@dataclass
class FinalOutputActivityResult:
    """Final output plus compact provider audit metadata."""

    output: FinalOutputData
    provider: str
    model: str


@dataclass
class BusinessActionExecutionRequest:
    """Validated action execution request with a deterministic retry key."""

    run_id: str
    workflow_id: str
    idempotency_key: str
    requested_at: str
    trigger: str
    supervisor_invocation: int
    action_index: int
    action_name: str
    arguments: dict[str, Any]
    available_actions: list[str]


@dataclass
class BusinessActionExecutionResult:
    """Typed result from the simulated business-action dispatcher."""

    action_name: str
    idempotency_key: str
    status: str
    summary: str
    persisted: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowSnapshot:
    """Read-only query/result view of workflow-owned state."""

    order_id: str
    workflow_status: WorkflowStatus
    order_state: OrderState
    pending_event_count: int
    seen_event_count: int
    is_sleeping: bool
    next_wake_at: str | None
    supervisor_invocation_count: int
    recent_timeline: list[TimelineEntry]
    completed: bool
    completion_reason: str | None
    paused: bool = False
    interrupted: bool = False
    pause_reason: str | None = None
    interrupt_reason: str | None = None
    additional_instructions: list[RunInstruction] = field(default_factory=list)
    memory_summary: CompactMemoryData | None = None
    final_output: FinalOutputData | None = None


@dataclass
class PersistenceTransitionRequest:
    """Idempotent Workflow transition persisted by a Temporal Activity."""

    run_id: str
    workflow_id: str
    temporal_run_id: str | None
    activity_key: str
    sequence: int
    recorded_at: str
    activity_type: str
    source: str
    status: str
    summary: str
    payload: dict[str, Any]
    event_name: str | None
    action_name: str | None
    external_event_id: str | None
    workflow_status: WorkflowStatus
    current_order_state: dict[str, Any]
    additional_instructions: list[dict[str, str]]
    next_wake_at: str | None
    completion_reason: str | None
    completed_at: str | None
    memory_summary: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None


def workflow_id_for_order(order_id: str) -> str:
    """Return the single deterministic workflow ID for an order."""

    return f"order-supervisor:{order_id}"
