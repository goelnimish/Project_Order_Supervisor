"""Persistence operations for supervisor configurations."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supervisor import SupervisorConfig


async def create_supervisor(
    session: AsyncSession,
    *,
    name: str,
    base_instruction: str,
    available_actions: Sequence[str],
    default_wake_seconds: int | None = None,
    wake_aggressiveness: str = "moderate",
    model_configuration: Mapping[str, Any] | None = None,
    supervisor_id: UUID | None = None,
) -> SupervisorConfig:
    """Create and flush a reusable supervisor configuration."""

    supervisor = SupervisorConfig(
        id=supervisor_id or uuid4(),
        name=name,
        base_instruction=base_instruction,
        available_actions=list(available_actions),
        default_wake_seconds=default_wake_seconds,
        wake_aggressiveness=wake_aggressiveness,
        model_configuration=deepcopy(dict(model_configuration or {})),
    )
    session.add(supervisor)
    await session.flush()
    return supervisor


async def get_supervisor(session: AsyncSession, supervisor_id: UUID) -> SupervisorConfig | None:
    """Return one supervisor configuration by primary key."""

    return await session.get(SupervisorConfig, supervisor_id)


async def list_supervisors(session: AsyncSession) -> list[SupervisorConfig]:
    """Return supervisor configurations in stable creation order."""

    result = await session.scalars(
        select(SupervisorConfig).order_by(SupervisorConfig.created_at, SupervisorConfig.id)
    )
    return list(result)


__all__ = ["create_supervisor", "get_supervisor", "list_supervisors"]
