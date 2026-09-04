"""Incoming order-event API contracts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderEventRequest(BaseModel):
    """Typed order event forwarded to the running Workflow as a Signal."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "event_type")
    @classmethod
    def strip_event_text(cls, value: str) -> str:
        """Reject blank event identifiers and names."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


__all__ = ["OrderEventRequest"]
