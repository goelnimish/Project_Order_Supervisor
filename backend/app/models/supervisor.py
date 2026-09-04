"""Reusable supervisor configuration persisted in PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.temporal.models import BUSINESS_ACTION_NAMES

if TYPE_CHECKING:
    from app.models.run import Run

ALLOWED_ACTION_NAMES = BUSINESS_ACTION_NAMES
WAKE_AGGRESSIVENESS_VALUES = ("low", "moderate", "high")


class SupervisorConfig(Base):
    """Reusable operator configuration supplied to an order Workflow."""

    __tablename__ = "supervisor_configs"
    __table_args__ = (
        CheckConstraint(
            "wake_aggressiveness IN ('low', 'moderate', 'high')",
            name="ck_supervisor_configs_wake_aggressiveness",
        ),
        CheckConstraint(
            "default_wake_seconds IS NULL OR default_wake_seconds BETWEEN 1 AND 86400",
            name="ck_supervisor_configs_default_wake_seconds",
        ),
        CheckConstraint(
            "jsonb_typeof(available_actions) = 'array'",
            name="ck_supervisor_configs_available_actions_array",
        ),
        CheckConstraint(
            "jsonb_typeof(model_config) = 'object'",
            name="ck_supervisor_configs_model_config_object",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    available_actions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    default_wake_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wake_aggressiveness: Mapped[str] = mapped_column(
        String(16), nullable=False, default="moderate", server_default=text("'moderate'")
    )
    model_configuration: Mapped[dict[str, Any]] = mapped_column(
        "model_config",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    runs: Mapped[list[Run]] = relationship(back_populates="supervisor")


__all__ = [
    "ALLOWED_ACTION_NAMES",
    "WAKE_AGGRESSIVENESS_VALUES",
    "SupervisorConfig",
]
