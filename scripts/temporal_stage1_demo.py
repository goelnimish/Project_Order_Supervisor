"""Run the complete Stage 1 Temporal scenario against the local development server."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from temporalio.client import WorkflowHandle

from app.config import get_settings
from app.temporal.models import (
    EventType,
    OrderContext,
    OrderEvent,
    WorkflowInput,
    WorkflowSnapshot,
    workflow_id_for_order,
)
from app.temporal.workflows import OrderSupervisorWorkflow
from app.temporal_client import connect_temporal

MIN_DEMO_WAKE_SECONDS = 5


def parse_args() -> argparse.Namespace:
    """Parse the intentionally small demo configuration surface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wake-seconds",
        type=int,
        default=10,
        help="Demo review interval in seconds (default: 10)",
    )
    return parser.parse_args()


def make_event(event_id: str, event_type: EventType) -> OrderEvent:
    """Create a timestamped event for the demo client."""

    return OrderEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=datetime.now(UTC).isoformat(),
        payload={"source": "stage1-demo"},
    )


async def wait_for_snapshot(
    handle: WorkflowHandle,
    predicate: Callable[[WorkflowSnapshot], bool],
    description: str,
    *,
    timeout_seconds: float = 20,
) -> WorkflowSnapshot:
    """Poll the query until a workflow task has exposed the expected state."""

    deadline = time.monotonic() + timeout_seconds
    latest: WorkflowSnapshot | None = None
    while time.monotonic() < deadline:
        latest = await handle.query(OrderSupervisorWorkflow.get_state)
        if predicate(latest):
            return latest
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {description}; latest state: {latest!r}")


def print_snapshot(label: str, snapshot: WorkflowSnapshot) -> None:
    """Print a compact, readable checkpoint."""

    print(f"\n=== {label} ===")
    print(
        json.dumps(
            {
                "order_id": snapshot.order_id,
                "workflow_status": snapshot.workflow_status,
                "order_state": asdict(snapshot.order_state),
                "next_wake_at": snapshot.next_wake_at,
                "supervisor_invocation_count": snapshot.supervisor_invocation_count,
                "completed": snapshot.completed,
                "completion_reason": snapshot.completion_reason,
                "latest_timeline_entries": [
                    asdict(entry) for entry in snapshot.recent_timeline[-3:]
                ],
            },
            indent=2,
        )
    )


async def run_demo(wake_seconds: int) -> None:
    """Exercise every required Stage 1 trigger in order."""

    if not MIN_DEMO_WAKE_SECONDS <= wake_seconds <= 24 * 60 * 60:
        raise ValueError(f"--wake-seconds must be between {MIN_DEMO_WAKE_SECONDS} and 86400")

    settings = get_settings()
    client = await connect_temporal(settings)
    order_id = f"stage1-demo-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    workflow_id = workflow_id_for_order(order_id)
    print(f"Starting demo order: {order_id}")
    print(f"Temporal workflow ID: {workflow_id}")

    handle = await client.start_workflow(
        OrderSupervisorWorkflow.run,
        WorkflowInput(
            order=OrderContext(
                order_id=order_id,
                customer_id="demo-customer",
                initial_state={"source": "stage1-demo"},
            ),
            supervisor_instructions="Monitor this demo order and surface important exceptions.",
            demo_wake_interval_seconds=wake_seconds,
        ),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )

    completed = False
    try:
        initial = await wait_for_snapshot(
            handle,
            lambda state: state.supervisor_invocation_count == 1 and state.is_sleeping,
            "the initial fake-supervisor review",
        )
        print_snapshot("1. Initial supervisor review", initial)

        await handle.signal(
            OrderSupervisorWorkflow.receive_event,
            make_event("demo-payment-confirmed", EventType.PAYMENT_CONFIRMED),
        )
        routine = await wait_for_snapshot(
            handle,
            lambda state: (
                state.order_state.payment == "confirmed"
                and any(
                    entry.entry_type == "supervisor_wake_suppressed"
                    and entry.details.get("event_id") == "demo-payment-confirmed"
                    for entry in state.recent_timeline
                )
            ),
            "routine-event processing",
        )
        if routine.supervisor_invocation_count != initial.supervisor_invocation_count:
            raise RuntimeError("Routine event unexpectedly invoked the fake supervisor")
        print_snapshot("2. Routine event suppressed", routine)

        await handle.signal(
            OrderSupervisorWorkflow.receive_event,
            make_event("demo-shipment-delayed", EventType.SHIPMENT_DELAYED),
        )
        important = await wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count == routine.supervisor_invocation_count + 1
                and state.is_sleeping
                and any(
                    entry.entry_type == "supervisor_decision"
                    and entry.details.get("trigger") == "event:shipment_delayed"
                    for entry in state.recent_timeline
                )
            ),
            "important-event supervisor review",
        )
        print_snapshot("3. Important event woke supervisor", important)

        scheduled = await wait_for_snapshot(
            handle,
            lambda state: (
                state.supervisor_invocation_count >= important.supervisor_invocation_count + 1
                and any(
                    entry.entry_type == "supervisor_decision"
                    and entry.details.get("trigger") == "scheduled_wake"
                    for entry in state.recent_timeline
                )
            ),
            "one scheduled durable wake",
            timeout_seconds=wake_seconds + 15,
        )
        print_snapshot("4. Scheduled wake invoked supervisor", scheduled)

        await handle.signal(
            OrderSupervisorWorkflow.receive_event,
            make_event("demo-delivered", EventType.DELIVERED),
        )
        final_result = await handle.result()
        completed = True
        print_snapshot("5. Deterministic delivered completion", final_result)
        print("\nSTAGE 1 DEMO PASSED")
    finally:
        if not completed:
            await handle.terminate("Stage 1 demo cleanup after an incomplete run")


def main() -> None:
    """Run the async demo from the command line."""

    args = parse_args()
    asyncio.run(run_demo(args.wake_seconds))


if __name__ == "__main__":
    main()
