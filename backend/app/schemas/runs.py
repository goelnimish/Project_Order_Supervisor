"""Run resource API contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.temporal.models import WorkflowStatus


class OrderContextRequest(BaseModel):
    """Structured order details supplied when a run starts."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = Field(default=None, max_length=200)
    initial_state: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    """Payload for starting one supervisor Workflow for an order."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=128)
    supervisor_config_id: UUID
    order_context: OrderContextRequest

    @field_validator("order_id")
    @classmethod
    def strip_order_id(cls, value: str) -> str:
        """Reject whitespace-only order identifiers."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class RunResponse(BaseModel):
    """PostgreSQL read-model snapshot for a supervisor run."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_id: str
    workflow_id: str
    temporal_run_id: str | None
    supervisor_config_id: UUID
    status: WorkflowStatus
    current_order_state: dict[str, Any]
    order_context: dict[str, Any]
    additional_instructions: list[dict[str, Any]]
    memory_summary: dict[str, Any] | None
    final_output: dict[str, Any] | None
    next_wake_at: datetime | None
    started_at: datetime
    completed_at: datetime | None
    completion_reason: str | None
    created_at: datetime
    updated_at: datetime


class ActivityResponse(BaseModel):
    """One row in the persistent unified activity timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    activity_key: str
    activity_type: str
    source: str
    event_name: str | None
    action_name: str | None
    status: str
    summary: str
    payload: dict[str, Any]
    external_event_id: str | None
    created_at: datetime


class WorkflowStateResponse(BaseModel):
    """Optional live Temporal Query state embedded in a run detail response."""

    order_id: str
    workflow_status: WorkflowStatus
    order_state: dict[str, Any]
    pending_event_count: int
    seen_event_count: int
    is_sleeping: bool
    next_wake_at: str | None
    supervisor_invocation_count: int
    recent_timeline: list[dict[str, Any]]
    completed: bool
    completion_reason: str | None
    paused: bool
    interrupted: bool
    pause_reason: str | None
    interrupt_reason: str | None
    additional_instructions: list[dict[str, Any]]
    memory_summary: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None


class RunDetailResponse(RunResponse):
    """Persisted run plus timeline and best-effort live Workflow state."""

    activities: list[ActivityResponse]
    workflow_state: WorkflowStateResponse | None = None
