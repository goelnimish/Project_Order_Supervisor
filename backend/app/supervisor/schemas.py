"""Strict structured contracts shared by every supervisor provider."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.temporal.models import (
    AgentDecisionData,
    BusinessActionName,
    BusinessActionProposalData,
    CompactMemoryData,
    FinalOutputData,
)

MAX_MEMORY_ITEMS = 6


class DecisionUrgency(StrEnum):
    """Small, display-safe urgency vocabulary for supervisor decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BusinessActionArguments(BaseModel):
    """Typed content accepted by every simulated Stage 3 action."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2_000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        lowered = stripped.lower()
        forbidden_lifecycle_requests = (
            "close_workflow",
            "close workflow",
            "terminate workflow",
            "complete workflow",
            "pause workflow",
            "resume workflow",
        )
        if any(phrase in lowered for phrase in forbidden_lifecycle_requests):
            raise ValueError("must not request a Workflow lifecycle transition")
        return stripped


class BusinessActionProposal(BaseModel):
    """One exact allowlisted action plus validated arguments."""

    model_config = ConfigDict(extra="forbid")

    action_name: BusinessActionName
    arguments: BusinessActionArguments


class CompactMemory(BaseModel):
    """Bounded rolling memory; it deliberately is not a retrieval system."""

    model_config = ConfigDict(extra="forbid")

    order_state: str = Field(default="", max_length=500)
    important_facts: list[str] = Field(default_factory=list, max_length=MAX_MEMORY_ITEMS)
    open_issues: list[str] = Field(default_factory=list, max_length=MAX_MEMORY_ITEMS)
    actions_taken: list[str] = Field(default_factory=list, max_length=MAX_MEMORY_ITEMS)
    active_constraints: list[str] = Field(default_factory=list, max_length=MAX_MEMORY_ITEMS)
    next_review: str = Field(default="", max_length=300)

    @field_validator(
        "important_facts",
        "open_issues",
        "actions_taken",
        "active_constraints",
    )
    @classmethod
    def normalize_items(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            stripped = value.strip()
            if not stripped:
                continue
            if len(stripped) > 300:
                raise ValueError("memory items must be at most 300 characters")
            if stripped not in normalized:
                normalized.append(stripped)
        return normalized


class AgentDecision(BaseModel):
    """Only validated instances may reach Workflow action orchestration."""

    model_config = ConfigDict(extra="forbid")

    decision_summary: str = Field(min_length=1, max_length=600)
    urgency: DecisionUrgency
    should_act: bool
    actions: list[BusinessActionProposal] = Field(max_length=5)
    memory_update: CompactMemory
    next_wake_seconds: int = Field(ge=1, le=86_400)
    completion_recommendation: bool

    @field_validator("decision_summary")
    @classmethod
    def strip_summary(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def actions_match_flag(self) -> AgentDecision:
        if self.should_act != bool(self.actions):
            raise ValueError("should_act must match whether actions are present")
        return self


class FinalOutput(BaseModel):
    """Validated report generated after a Workflow-owned terminal decision."""

    model_config = ConfigDict(extra="forbid")

    final_summary: str = Field(min_length=1, max_length=2_000)
    important_actions: list[str] = Field(min_length=1, max_length=12)
    key_learnings: list[str] = Field(min_length=1, max_length=12)
    recommendations: list[str] = Field(min_length=1, max_length=12)

    @field_validator("final_summary")
    @classmethod
    def strip_final_summary(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("important_actions", "key_learnings", "recommendations")
    @classmethod
    def normalize_report_items(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            stripped = value.strip()
            if not stripped:
                raise ValueError("report items must not be blank")
            if len(stripped) > 500:
                raise ValueError("report items must be at most 500 characters")
            normalized.append(stripped)
        return normalized


def memory_from_data(memory: CompactMemoryData | None) -> CompactMemory:
    """Validate Workflow memory before a provider uses it."""

    if memory is None:
        return CompactMemory()
    return CompactMemory.model_validate(
        {
            "order_state": memory.order_state,
            "important_facts": memory.important_facts,
            "open_issues": memory.open_issues,
            "actions_taken": memory.actions_taken,
            "active_constraints": memory.active_constraints,
            "next_review": memory.next_review,
        }
    )


def memory_to_data(memory: CompactMemory) -> CompactMemoryData:
    """Convert a validated model to the default Temporal dataclass wire format."""

    return CompactMemoryData(**memory.model_dump(mode="json"))


def decision_to_data(
    decision: AgentDecision,
    *,
    trigger: str,
    provider: str,
    model: str,
) -> AgentDecisionData:
    """Convert a validated provider result to a serialization-safe DTO."""

    return AgentDecisionData(
        trigger=trigger,
        decision_summary=decision.decision_summary,
        should_act=decision.should_act,
        actions=[
            BusinessActionProposalData(
                action_name=action.action_name.value,
                arguments=action.arguments.model_dump(mode="json"),
            )
            for action in decision.actions
        ],
        memory_update=memory_to_data(decision.memory_update),
        next_wake_seconds=decision.next_wake_seconds,
        completion_recommendation=decision.completion_recommendation,
        urgency=decision.urgency.value,
        provider=provider,
        model=model,
    )


def final_output_to_data(output: FinalOutput) -> FinalOutputData:
    """Convert a validated final report to the Temporal wire format."""

    return FinalOutputData(**output.model_dump(mode="json"))


__all__ = [
    "AgentDecision",
    "BusinessActionArguments",
    "BusinessActionProposal",
    "CompactMemory",
    "DecisionUrgency",
    "FinalOutput",
    "decision_to_data",
    "final_output_to_data",
    "memory_from_data",
    "memory_to_data",
]
