"""Read-only PostgreSQL aggregation for minimal operator analytics."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Float, and_, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.analytics.schemas import (
    ActionDistribution,
    GlobalAnalyticsResponse,
    RunAnalyticsResponse,
    RunCounts,
)
from app.models.activity import Activity
from app.models.run import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES, Run
from app.temporal.models import BUSINESS_ACTION_NAMES

EVENT_RECEIVED = "order_event_received"
WAKE_SUPPRESSED = "supervisor_wake_suppressed"
WAKE_REQUESTED = "supervisor_wake_requested"
SUPERVISOR_INVOCATION = "supervisor_invocation"
BUSINESS_ACTION_EXECUTED = "business_action_executed"

# Only this Activity-owned row is proof that an action executed. Supervisor decisions,
# rejected proposals, failures, and arbitrary action names are deliberately excluded.
EXECUTED_ACTION_FILTER = and_(
    Activity.activity_type == BUSINESS_ACTION_EXECUTED,
    Activity.source == "business_action",
    Activity.status == "executed",
    Activity.action_name.in_(BUSINESS_ACTION_NAMES),
)


async def get_global_analytics(session: AsyncSession) -> GlobalAnalyticsResponse:
    """Return the requested global metrics without consulting Temporal or the model."""

    run_values = (
        (
            await session.execute(
                select(
                    func.count(Run.id).label("total"),
                    func.count(Run.id).filter(Run.status.in_(ACTIVE_RUN_STATUSES)).label("active"),
                    func.count(Run.id).filter(Run.status == "completed").label("completed"),
                    func.count(Run.id).filter(Run.status == "terminated").label("terminated"),
                )
            )
        )
        .mappings()
        .one()
    )

    action_count_columns = [
        func.count(Activity.id)
        .filter(and_(EXECUTED_ACTION_FILTER, Activity.action_name == action_name))
        .label(action_name)
        for action_name in BUSINESS_ACTION_NAMES
    ]
    activity_values = (
        (
            await session.execute(
                select(
                    func.count(Activity.id)
                    .filter(EXECUTED_ACTION_FILTER)
                    .label("business_actions_executed"),
                    func.count(Activity.id)
                    .filter(Activity.activity_type == WAKE_SUPPRESSED)
                    .label("wake_suppressions"),
                    func.count(Activity.id)
                    .filter(Activity.activity_type.in_((WAKE_SUPPRESSED, WAKE_REQUESTED)))
                    .label("eligible_wake_decisions"),
                    *action_count_columns,
                )
            )
        )
        .mappings()
        .one()
    )

    wake_denominator = int(activity_values["eligible_wake_decisions"] or 0)
    wake_suppression_rate = (
        int(activity_values["wake_suppressions"] or 0) / wake_denominator
        if wake_denominator
        else None
    )

    return GlobalAnalyticsResponse(
        runs=RunCounts(
            total=int(run_values["total"] or 0),
            active=int(run_values["active"] or 0),
            completed=int(run_values["completed"] or 0),
            terminated=int(run_values["terminated"] or 0),
        ),
        wake_suppression_rate=wake_suppression_rate,
        business_actions_executed=int(activity_values["business_actions_executed"] or 0),
        average_time_to_first_intervention_seconds=(
            await _average_time_to_first_intervention_seconds(session)
        ),
        action_distribution=ActionDistribution(
            **{
                action_name: int(activity_values[action_name] or 0)
                for action_name in BUSINESS_ACTION_NAMES
            }
        ),
    )


async def get_run_analytics(
    session: AsyncSession,
    run_id: UUID,
) -> RunAnalyticsResponse | None:
    """Return one run's duration and canonical persisted activity counts."""

    terminal_duration = cast(
        func.extract("epoch", Run.completed_at - Run.started_at),
        Float,
    )
    active_duration = cast(
        func.extract("epoch", func.current_timestamp() - Run.started_at),
        Float,
    )
    duration = case(
        (
            and_(Run.status.in_(TERMINAL_RUN_STATUSES), Run.completed_at.is_not(None)),
            terminal_duration,
        ),
        (Run.status.in_(ACTIVE_RUN_STATUSES), active_duration),
        else_=None,
    ).label("duration_seconds")

    values = (
        (
            await session.execute(
                select(
                    Run.id.label("run_id"),
                    Run.order_id,
                    duration,
                    func.count(Activity.id)
                    .filter(Activity.activity_type == EVENT_RECEIVED)
                    .label("events_received"),
                    func.count(Activity.id)
                    .filter(Activity.activity_type == SUPERVISOR_INVOCATION)
                    .label("supervisor_invocations"),
                    func.count(Activity.id)
                    .filter(Activity.activity_type == WAKE_SUPPRESSED)
                    .label("wake_suppressions"),
                    func.count(Activity.id)
                    .filter(EXECUTED_ACTION_FILTER)
                    .label("business_actions_executed"),
                )
                .outerjoin(Activity, Activity.run_id == Run.id)
                .where(Run.id == run_id)
                .group_by(
                    Run.id,
                    Run.order_id,
                    Run.status,
                    Run.started_at,
                    Run.completed_at,
                )
            )
        )
        .mappings()
        .one_or_none()
    )

    if values is None:
        return None
    raw_duration = values["duration_seconds"]
    return RunAnalyticsResponse(
        run_id=values["run_id"],
        order_id=values["order_id"],
        duration_seconds=float(raw_duration) if raw_duration is not None else None,
        events_received=int(values["events_received"] or 0),
        supervisor_invocations=int(values["supervisor_invocations"] or 0),
        wake_suppressions=int(values["wake_suppressions"] or 0),
        business_actions_executed=int(values["business_actions_executed"] or 0),
    )


async def _average_time_to_first_intervention_seconds(
    session: AsyncSession,
) -> float | None:
    """Average receipt-to-action time using the closest defensible persisted pairing.

    Executed action rows retain their event type in ``payload.trigger`` but do not retain
    an event ID. Each action is therefore paired with the closest preceding wake request
    of the same event type, then with that wake's accepted event row. The earliest matched
    action per run is its first intervention. Startup, scheduled, unmatched, and negative
    intervals are excluded; no eligible run produces ``None``.
    """

    action_trigger = Activity.payload["trigger"].as_string()
    event_actions = (
        select(
            Activity.id.label("action_id"),
            Activity.run_id,
            Activity.activity_key.label("action_key"),
            Activity.created_at.label("action_at"),
            action_trigger.label("action_trigger"),
        )
        .where(
            EXECUTED_ACTION_FILTER,
            action_trigger.like("event:%"),
        )
        .cte("analytics_event_actions")
    )

    wake = aliased(Activity, name="analytics_wake")
    received = aliased(Activity, name="analytics_received_event")
    candidate_pairs = (
        select(
            event_actions.c.action_id,
            event_actions.c.run_id,
            event_actions.c.action_key,
            event_actions.c.action_at,
            received.created_at.label("event_received_at"),
            func.row_number()
            .over(
                partition_by=event_actions.c.action_id,
                order_by=(wake.created_at.desc(), wake.activity_key.desc()),
            )
            .label("wake_rank"),
        )
        .select_from(event_actions)
        .join(
            wake,
            and_(
                wake.run_id == event_actions.c.run_id,
                wake.activity_type == WAKE_REQUESTED,
                wake.created_at <= event_actions.c.action_at,
                event_actions.c.action_trigger == literal("event:") + wake.event_name,
                wake.external_event_id.is_not(None),
            ),
        )
        .join(
            received,
            and_(
                received.run_id == wake.run_id,
                received.activity_type == EVENT_RECEIVED,
                received.external_event_id == wake.external_event_id,
                received.created_at <= wake.created_at,
            ),
        )
        .cte("analytics_candidate_interventions")
    )

    matched_actions = (
        select(
            candidate_pairs.c.run_id,
            candidate_pairs.c.action_key,
            candidate_pairs.c.action_at,
            candidate_pairs.c.event_received_at,
        )
        .where(
            candidate_pairs.c.wake_rank == 1,
            candidate_pairs.c.action_at >= candidate_pairs.c.event_received_at,
        )
        .cte("analytics_matched_interventions")
    )
    ranked_interventions = select(
        matched_actions.c.run_id,
        matched_actions.c.action_at,
        matched_actions.c.event_received_at,
        func.row_number()
        .over(
            partition_by=matched_actions.c.run_id,
            order_by=(matched_actions.c.action_at, matched_actions.c.action_key),
        )
        .label("intervention_rank"),
    ).cte("analytics_ranked_interventions")
    latency_seconds = func.extract(
        "epoch",
        ranked_interventions.c.action_at - ranked_interventions.c.event_received_at,
    )
    average = await session.scalar(
        select(func.avg(latency_seconds)).where(
            ranked_interventions.c.intervention_rank == 1,
            latency_seconds >= 0,
        )
    )
    return float(average) if average is not None else None


__all__ = ["get_global_analytics", "get_run_analytics"]
