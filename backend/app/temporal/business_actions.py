"""Strict, retry-safe simulated business-action Temporal Activity."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.database import async_session_factory
from app.repositories.activities import append_activity_idempotently
from app.supervisor.schemas import BusinessActionArguments
from app.temporal.models import (
    BUSINESS_ACTION_NAMES,
    BusinessActionExecutionRequest,
    BusinessActionExecutionResult,
    BusinessActionName,
)

EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME = "execute_business_action"


@activity.defn(name=EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME)
async def execute_business_action(
    request: BusinessActionExecutionRequest,
) -> BusinessActionExecutionResult:
    """Validate, simulate, and persist exactly one allowlisted action."""

    try:
        run_id = UUID(request.run_id)
        action_name = BusinessActionName(request.action_name)
        arguments = BusinessActionArguments.model_validate(request.arguments)
    except (ValueError, ValidationError):
        raise ApplicationError(
            "The proposed business action is malformed or unknown.",
            type="InvalidBusinessAction",
            non_retryable=True,
        ) from None

    configured_actions = set(request.available_actions)
    if action_name.value not in configured_actions:
        raise ApplicationError(
            "The proposed business action is disabled for this supervisor.",
            type="DisabledBusinessAction",
            non_retryable=True,
        )
    if not configured_actions.issubset(BUSINESS_ACTION_NAMES):
        raise ApplicationError(
            "The supervisor action allowlist is invalid.",
            type="InvalidBusinessActionAllowlist",
            non_retryable=True,
        )

    summary, simulation = _dispatch_simulation(action_name, arguments)
    payload = {
        "arguments": arguments.model_dump(mode="json"),
        "simulation": simulation,
        "trigger": request.trigger,
        "supervisor_invocation": request.supervisor_invocation,
        "action_index": request.action_index,
        "idempotency_key": request.idempotency_key,
    }

    try:
        async with async_session_factory.begin() as session:
            persisted, created = await append_activity_idempotently(
                session,
                run_id=run_id,
                activity_key=request.idempotency_key,
                activity_type="business_action_executed",
                source="business_action",
                action_name=action_name.value,
                status="executed",
                summary=summary,
                payload=payload,
                created_at=datetime.fromisoformat(request.requested_at),
            )
            if not created and (
                persisted.action_name != action_name.value
                or persisted.status != "executed"
                or persisted.payload != payload
            ):
                raise ApplicationError(
                    "The business-action idempotency key conflicts with prior evidence.",
                    type="BusinessActionIdempotencyConflict",
                    non_retryable=True,
                )
    except ApplicationError:
        raise
    except (SQLAlchemyError, ValueError):
        raise ApplicationError(
            "Business-action persistence is temporarily unavailable.",
            type="BusinessActionPersistenceError",
        ) from None

    return BusinessActionExecutionResult(
        action_name=action_name.value,
        idempotency_key=request.idempotency_key,
        status="executed",
        summary=summary,
        persisted=True,
        details={"new_record": created, **simulation},
    )


def _dispatch_simulation(
    action_name: BusinessActionName,
    arguments: BusinessActionArguments,
) -> tuple[str, dict[str, Any]]:
    """Use explicit handlers; model text never selects an arbitrary callable."""

    if action_name is BusinessActionName.MESSAGE_FULFILLMENT_TEAM:
        return _message_fulfillment_team(arguments)
    if action_name is BusinessActionName.MESSAGE_PAYMENTS_TEAM:
        return _message_payments_team(arguments)
    if action_name is BusinessActionName.MESSAGE_LOGISTICS_TEAM:
        return _message_logistics_team(arguments)
    if action_name is BusinessActionName.MESSAGE_CUSTOMER:
        return _message_customer(arguments)
    if action_name is BusinessActionName.CREATE_INTERNAL_NOTE:
        return _create_internal_note(arguments)
    raise AssertionError("BusinessActionName exhaustiveness failure")


def _message_fulfillment_team(
    _arguments: BusinessActionArguments,
) -> tuple[str, dict[str, str]]:
    return (
        "Simulated a message to the fulfillment team.",
        {"destination": "fulfillment_team", "delivery": "simulated"},
    )


def _message_payments_team(
    _arguments: BusinessActionArguments,
) -> tuple[str, dict[str, str]]:
    return (
        "Simulated a message to the payments team.",
        {"destination": "payments_team", "delivery": "simulated"},
    )


def _message_logistics_team(
    _arguments: BusinessActionArguments,
) -> tuple[str, dict[str, str]]:
    return (
        "Simulated a message to the logistics team.",
        {"destination": "logistics_team", "delivery": "simulated"},
    )


def _message_customer(
    _arguments: BusinessActionArguments,
) -> tuple[str, dict[str, str]]:
    return (
        "Simulated a message to the customer.",
        {"destination": "customer", "delivery": "simulated"},
    )


def _create_internal_note(
    _arguments: BusinessActionArguments,
) -> tuple[str, dict[str, str]]:
    return (
        "Created a simulated internal order note.",
        {"destination": "internal_order_record", "delivery": "simulated"},
    )


__all__ = ["EXECUTE_BUSINESS_ACTION_ACTIVITY_NAME", "execute_business_action"]
