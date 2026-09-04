"""Live instruction and lifecycle-control API contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InstructionRequest(BaseModel):
    """Human instruction added to an already-running Workflow."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=5_000)

    @field_validator("instruction")
    @classmethod
    def strip_instruction(cls, value: str) -> str:
        """Reject an empty live instruction."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class PauseRunRequest(BaseModel):
    """Optional operator rationale for pausing automation."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1_000)


class InterruptRunRequest(BaseModel):
    """Required human-review rationale for interrupting automation."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        """Reject a blank human-review reason."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class TerminateRunRequest(InterruptRunRequest):
    """Operator rationale for graceful Workflow termination."""


class SignalAcceptedResponse(BaseModel):
    """Acknowledgement that Temporal accepted an asynchronous Signal."""

    status: Literal["accepted"] = "accepted"
    run_id: UUID
    workflow_id: str
    signal: str


__all__ = [
    "InstructionRequest",
    "InterruptRunRequest",
    "PauseRunRequest",
    "SignalAcceptedResponse",
    "TerminateRunRequest",
]
