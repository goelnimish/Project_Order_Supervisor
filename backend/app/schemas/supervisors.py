"""Supervisor configuration API contracts."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.temporal.models import BUSINESS_ACTION_NAMES, BusinessActionName


class WakeAggressiveness(StrEnum):
    """Small Stage 2 wake-policy configuration surface."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class SupervisorModelConfiguration(BaseModel):
    """Secret-free model metadata retained for the later real-LLM stage."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)


class SupervisorCreate(BaseModel):
    """Payload for creating a reusable supervisor configuration."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    base_instruction: str = Field(min_length=1, max_length=10_000)
    available_actions: list[BusinessActionName] = Field(
        default_factory=lambda: [BusinessActionName(name) for name in BUSINESS_ACTION_NAMES]
    )
    default_wake_seconds: int = Field(default=300, ge=1, le=86_400)
    wake_aggressiveness: WakeAggressiveness = WakeAggressiveness.MODERATE
    model_configuration: SupervisorModelConfiguration = Field(
        default_factory=SupervisorModelConfiguration,
        validation_alias="model_config",
        serialization_alias="model_config",
    )

    @field_validator("name", "base_instruction")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Reject values that only contain whitespace."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("available_actions")
    @classmethod
    def require_unique_actions(cls, actions: list[BusinessActionName]) -> list[BusinessActionName]:
        """Keep the allow-list explicit, non-empty, and duplicate-free."""

        if not actions:
            raise ValueError("at least one available action is required")
        if len(actions) != len(set(actions)):
            raise ValueError("available actions must not contain duplicates")
        return actions


class SupervisorResponse(BaseModel):
    """Stored supervisor configuration returned by the API."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID
    name: str
    base_instruction: str
    available_actions: list[BusinessActionName]
    default_wake_seconds: int
    wake_aggressiveness: WakeAggressiveness
    model_configuration: SupervisorModelConfiguration = Field(
        validation_alias="model_configuration",
        serialization_alias="model_config",
    )
    created_at: datetime
    updated_at: datetime
