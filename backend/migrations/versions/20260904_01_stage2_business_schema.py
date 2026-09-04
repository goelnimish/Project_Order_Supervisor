"""Create the Stage 2 business persistence schema.

Revision ID: 20260904_01
Revises:
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create supervisor configurations, run snapshots, and activity history."""

    op.create_table(
        "supervisor_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_instruction", sa.Text(), nullable=False),
        sa.Column(
            "available_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("default_wake_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "wake_aggressiveness",
            sa.String(length=16),
            server_default=sa.text("'moderate'"),
            nullable=False,
        ),
        sa.Column(
            "model_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(available_actions) = 'array'",
            name="ck_supervisor_configs_available_actions_array",
        ),
        sa.CheckConstraint(
            "default_wake_seconds IS NULL OR default_wake_seconds BETWEEN 1 AND 86400",
            name="ck_supervisor_configs_default_wake_seconds",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(model_config) = 'object'",
            name="ck_supervisor_configs_model_config_object",
        ),
        sa.CheckConstraint(
            "wake_aggressiveness IN ('low', 'moderate', 'high')",
            name="ck_supervisor_configs_wake_aggressiveness",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_id", sa.String(length=256), nullable=False),
        sa.Column("temporal_run_id", sa.String(length=128), nullable=True),
        sa.Column("supervisor_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'starting'"),
            nullable=False,
        ),
        sa.Column(
            "current_order_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("order_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "additional_instructions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("memory_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(additional_instructions) = 'array'",
            name="ck_runs_additional_instructions_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(current_order_state) = 'object'",
            name="ck_runs_current_order_state_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(order_context) = 'object'", name="ck_runs_order_context_object"
        ),
        sa.CheckConstraint(
            "status IN ('starting', 'running', 'sleeping', 'paused', 'interrupted', "
            "'completed', 'terminated', 'failed')",
            name="ck_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["supervisor_config_id"], ["supervisor_configs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("temporal_run_id", name="uq_runs_temporal_run_id"),
    )
    op.create_index("ix_runs_supervisor_config_id", "runs", ["supervisor_config_id"], unique=False)
    op.create_index("ix_runs_status", "runs", ["status"], unique=False)
    op.create_index("ix_runs_workflow_id", "runs", ["workflow_id"], unique=False)
    op.create_index(
        "uq_runs_active_order_id",
        "runs",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('starting', 'running', 'sleeping', 'paused', 'interrupted')"
        ),
    )

    op.create_table(
        "activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_key", sa.String(length=320), nullable=False),
        sa.Column("activity_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("event_name", sa.String(length=128), nullable=True),
        sa.Column("action_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("external_event_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_activities_payload_object"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("activity_key", name="uq_activities_activity_key"),
    )
    op.create_index(
        "ix_activities_external_event_id", "activities", ["external_event_id"], unique=False
    )
    op.create_index(
        "ix_activities_run_created_at", "activities", ["run_id", "created_at"], unique=False
    )


def downgrade() -> None:
    """Remove the Stage 2 business schema in reverse dependency order."""

    op.drop_index("ix_activities_run_created_at", table_name="activities")
    op.drop_index("ix_activities_external_event_id", table_name="activities")
    op.drop_table("activities")

    op.drop_index(
        "uq_runs_active_order_id",
        table_name="runs",
        postgresql_where=sa.text(
            "status IN ('starting', 'running', 'sleeping', 'paused', 'interrupted')"
        ),
    )
    op.drop_index("ix_runs_workflow_id", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_supervisor_config_id", table_name="runs")
    op.drop_table("runs")

    op.drop_table("supervisor_configs")
