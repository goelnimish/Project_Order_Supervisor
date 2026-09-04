"""Typed API responses for the minimal Stage 3.5 analytics layer."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsSchema(BaseModel):
    """Reject accidental additions to the intentionally small response contract."""

    model_config = ConfigDict(extra="forbid")


class RunCounts(AnalyticsSchema):
    """Global counts using the existing persisted run lifecycle statuses."""

    total: int = Field(ge=0)
    active: int = Field(ge=0)
    completed: int = Field(ge=0)
    terminated: int = Field(ge=0)


class ActionDistribution(AnalyticsSchema):
    """Executed-action counts for the exact five required business actions."""

    message_fulfillment_team: int = Field(default=0, ge=0)
    message_payments_team: int = Field(default=0, ge=0)
    message_logistics_team: int = Field(default=0, ge=0)
    message_customer: int = Field(default=0, ge=0)
    create_internal_note: int = Field(default=0, ge=0)


class GlobalAnalyticsResponse(AnalyticsSchema):
    """Aggregate operator metrics derived only from PostgreSQL records."""

    runs: RunCounts
    wake_suppression_rate: float | None = Field(default=None, ge=0, le=1)
    business_actions_executed: int = Field(ge=0)
    average_time_to_first_intervention_seconds: float | None = Field(default=None, ge=0)
    action_distribution: ActionDistribution


class RunAnalyticsResponse(AnalyticsSchema):
    """Requested analytics for one persisted supervisor run."""

    run_id: UUID
    order_id: str
    duration_seconds: float | None = None
    events_received: int = Field(ge=0)
    supervisor_invocations: int = Field(ge=0)
    wake_suppressions: int = Field(ge=0)
    business_actions_executed: int = Field(ge=0)


__all__ = [
    "ActionDistribution",
    "GlobalAnalyticsResponse",
    "RunAnalyticsResponse",
    "RunCounts",
]
