"""Temporal run snapshot persisted for API reads."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.activity import Activity
    from app.models.supervisor import SupervisorConfig

ACTIVE_RUN_STATUSES = ("starting", "running", "sleeping", "paused", "interrupted")
TERMINAL_RUN_STATUSES = ("completed", "terminated", "failed")
RUN_STATUS_VALUES = ACTIVE_RUN_STATUSES + TERMINAL_RUN_STATUSES


class Run(Base):
    """PostgreSQL snapshot of a Temporal-owned order supervision run."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('starting', 'running', 'sleeping', 'paused', 'interrupted', "
            "'completed', 'terminated', 'failed')",
            name="ck_runs_status",
        ),
        CheckConstraint(
            "jsonb_typeof(current_order_state) = 'object'",
            name="ck_runs_current_order_state_object",
        ),
        CheckConstraint(
            "jsonb_typeof(order_context) = 'object'",
            name="ck_runs_order_context_object",
        ),
        CheckConstraint(
            "jsonb_typeof(additional_instructions) = 'array'",
            name="ck_runs_additional_instructions_array",
        ),
        CheckConstraint(
            "final_output IS NULL OR jsonb_typeof(final_output) = 'object'",
            name="ck_runs_final_output_object",
        ),
        Index(
            "uq_runs_active_order_id",
            "order_id",
            unique=True,
            postgresql_where=text(
                "status IN ('starting', 'running', 'sleeping', 'paused', 'interrupted')"
            ),
        ),
        Index("ix_runs_status", "status"),
        Index("ix_runs_workflow_id", "workflow_id"),
        UniqueConstraint("temporal_run_id", name="uq_runs_temporal_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(256), nullable=False)
    temporal_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supervisor_config_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("supervisor_configs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="starting", server_default=text("'starting'")
    )
    current_order_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    order_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    additional_instructions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    memory_summary: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    final_output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    next_wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    supervisor: Mapped[SupervisorConfig] = relationship(back_populates="runs")
    activities: Mapped[list[Activity]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


__all__ = [
    "ACTIVE_RUN_STATUSES",
    "RUN_STATUS_VALUES",
    "TERMINAL_RUN_STATUSES",
    "Run",
]
