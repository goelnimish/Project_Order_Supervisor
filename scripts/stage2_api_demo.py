"""Exercise the complete Stage 2 scenario through the public FastAPI API."""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

API_TIMEOUT_SECONDS = 10.0
CONVERGENCE_TIMEOUT_SECONDS = 25.0
POLL_SECONDS = 0.25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="FastAPI base URL (default: http://127.0.0.1:8000)",
    )
    return parser.parse_args()


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    expected_status: int,
) -> Any:
    response = await client.request(method, path, json=json_body)
    if response.status_code != expected_status:
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}; expected "
            f"{expected_status}: {response.text}"
        )
    return response.json()


async def wait_for_run(
    client: httpx.AsyncClient,
    run_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    description: str,
    *,
    timeout_seconds: float = CONVERGENCE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Poll the API read model until one eventually consistent condition is true."""

    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest = await request_json(
            client,
            "GET",
            f"/api/runs/{run_id}",
            expected_status=200,
        )
        if predicate(latest):
            return latest
        await asyncio.sleep(POLL_SECONDS)
    raise TimeoutError(f"Timed out waiting for {description}; latest run: {latest!r}")


def has_activity(
    run: dict[str, Any],
    activity_type: str,
    *,
    external_event_id: str | None = None,
    trigger: str | None = None,
) -> bool:
    for item in run["activities"]:
        if item["activity_type"] != activity_type:
            continue
        if external_event_id is not None and item["external_event_id"] != external_event_id:
            continue
        if trigger is not None and item["payload"].get("trigger") != trigger:
            continue
        return True
    return False


def invocation_count(run: dict[str, Any]) -> int:
    state = run.get("workflow_state")
    if state is None:
        raise RuntimeError("The live Workflow Query state is unavailable")
    return int(state["supervisor_invocation_count"])


def activity_count(run: dict[str, Any], activity_type: str) -> int:
    return sum(item["activity_type"] == activity_type for item in run["activities"])


def has_complete_stage2_trail(run: dict[str, Any]) -> bool:
    required_activity_types = {
        "workflow_started",
        "order_event_received",
        "supervisor_wake_suppressed",
        "supervisor_decision",
        "instruction_added",
        "workflow_paused",
        "workflow_resumed",
        "workflow_interrupted",
        "termination_requested",
        "workflow_terminated",
    }
    return (
        all(has_activity(run, activity_type) for activity_type in required_activity_types)
        and activity_count(run, "workflow_resumed") >= 2
    )


async def run_demo(api_url: str) -> None:
    unique = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    order_id = f"stage2-demo-{unique}"
    routine_event_id = f"stage2-payment-{unique}"
    important_event_id = f"stage2-delay-{unique}"
    paused_event_id = f"stage2-paused-failure-{unique}"
    live_instruction = "For this order, prioritize speed over cost."

    async with httpx.AsyncClient(
        base_url=api_url.rstrip("/"),
        timeout=API_TIMEOUT_SECONDS,
    ) as client:
        health = await request_json(client, "GET", "/health", expected_status=200)
        print(f"API health: {health['status']}")

        supervisor = await request_json(
            client,
            "POST",
            "/api/supervisors",
            json_body={
                "name": f"Stage 2 Demo Supervisor {unique}",
                "base_instruction": "Monitor this order and surface important exceptions.",
                "available_actions": [
                    "message_fulfillment_team",
                    "message_payments_team",
                    "message_logistics_team",
                    "message_customer",
                    "create_internal_note",
                ],
                "default_wake_seconds": 60,
                "wake_aggressiveness": "moderate",
                "model_config": {"provider": "deterministic"},
            },
            expected_status=201,
        )
        print(f"1. Supervisor configuration created through API: {supervisor['id']}")

        run = await request_json(
            client,
            "POST",
            "/api/runs",
            json_body={
                "order_id": order_id,
                "supervisor_config_id": supervisor["id"],
                "order_context": {
                    "customer_id": "stage2-demo-customer",
                    "initial_state": {"source": "stage2-api-demo"},
                },
            },
            expected_status=201,
        )
        run_id = run["id"]
        if run["workflow_id"] != f"order-supervisor:{order_id}":
            raise RuntimeError("API returned an unexpected Temporal Workflow ID")
        if run["temporal_run_id"] is None:
            raise RuntimeError("API did not persist the Temporal execution run ID")
        print(f"2. PostgreSQL run created and Temporal Workflow started: {run_id}")
        print(f"   Temporal Workflow ID: {run['workflow_id']}")

        initial = await wait_for_run(
            client,
            run_id,
            lambda item: (
                item["status"] == "sleeping"
                and item.get("workflow_state") is not None
                and invocation_count(item) == 1
                and has_activity(item, "workflow_started")
            ),
            "initial persisted supervisor review",
        )
        print("3. Run status and initial activity are visible through the API")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/events",
            json_body={
                "event_id": routine_event_id,
                "event_type": "payment_confirmed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {"source": "stage2-api-demo"},
            },
            expected_status=202,
        )
        routine = await wait_for_run(
            client,
            run_id,
            lambda item: (
                has_activity(
                    item,
                    "order_event_received",
                    external_event_id=routine_event_id,
                )
                and has_activity(
                    item,
                    "supervisor_wake_suppressed",
                    external_event_id=routine_event_id,
                )
            ),
            "routine event receipt and suppressed wake",
        )
        if invocation_count(routine) != invocation_count(initial):
            raise RuntimeError("Routine payment event unexpectedly invoked the supervisor")
        print("4. payment_confirmed Signal persisted; supervisor wake was suppressed")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/events",
            json_body={
                "event_id": important_event_id,
                "event_type": "shipment_delayed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {"delay_hours": 6},
            },
            expected_status=202,
        )
        await wait_for_run(
            client,
            run_id,
            lambda item: (
                item.get("workflow_state") is not None
                and invocation_count(item) == invocation_count(routine) + 1
                and has_activity(
                    item,
                    "order_event_received",
                    external_event_id=important_event_id,
                )
                and has_activity(
                    item,
                    "supervisor_decision",
                    trigger="event:shipment_delayed",
                )
            ),
            "important-event supervisor decision",
        )
        print("5. shipment_delayed Signal woke the fake supervisor; decision persisted")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/instructions",
            json_body={"instruction": live_instruction},
            expected_status=202,
        )
        await wait_for_run(
            client,
            run_id,
            lambda item: (
                any(
                    instruction["instruction"] == live_instruction
                    for instruction in item["additional_instructions"]
                )
                and item.get("workflow_state") is not None
                and any(
                    instruction["instruction"] == live_instruction
                    for instruction in item["workflow_state"]["additional_instructions"]
                )
                and has_activity(item, "instruction_added")
            ),
            "live instruction in PostgreSQL and Workflow state",
        )
        print("6. Live instruction entered the Workflow and PostgreSQL read model")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/pause",
            json_body={"reason": "Verify queued-event behavior."},
            expected_status=202,
        )
        paused = await wait_for_run(
            client,
            run_id,
            lambda item: (
                item["status"] == "paused"
                and item.get("workflow_state") is not None
                and item["workflow_state"]["paused"] is True
                and has_activity(item, "workflow_paused")
            ),
            "PAUSED state",
        )
        paused_invocations = invocation_count(paused)
        print("7. Workflow entered PAUSED state")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/events",
            json_body={
                "event_id": paused_event_id,
                "event_type": "payment_failed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {"source": "stage2-api-demo", "while_paused": True},
            },
            expected_status=202,
        )
        queued = await wait_for_run(
            client,
            run_id,
            lambda item: (
                has_activity(
                    item,
                    "order_event_received",
                    external_event_id=paused_event_id,
                )
                and item.get("workflow_state") is not None
                and item["workflow_state"]["paused"] is True
                and item["workflow_state"]["pending_event_count"] >= 1
            ),
            "important event queued while paused",
        )
        if invocation_count(queued) != paused_invocations:
            raise RuntimeError("A queued event invoked the supervisor while paused")
        print("8. Event queued while paused; supervisor invocation count stayed unchanged")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/resume",
            expected_status=202,
        )
        await wait_for_run(
            client,
            run_id,
            lambda item: (
                item["status"] == "sleeping"
                and item.get("workflow_state") is not None
                and invocation_count(item) == paused_invocations + 1
                and has_activity(
                    item,
                    "supervisor_decision",
                    trigger="event:payment_failed",
                )
                and has_activity(item, "workflow_resumed")
            ),
            "queued important event after resume",
        )
        print("9. Resume continued processing and handled the queued event")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/interrupt",
            json_body={"reason": "Operator needs urgent human review."},
            expected_status=202,
        )
        await wait_for_run(
            client,
            run_id,
            lambda item: (
                item["status"] == "interrupted"
                and item.get("workflow_state") is not None
                and item["workflow_state"]["interrupted"] is True
                and has_activity(item, "workflow_interrupted")
            ),
            "INTERRUPTED state",
        )
        print("10. Interrupt entered the human-review/INTERRUPTED state")

        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/resume",
            expected_status=202,
        )
        await wait_for_run(
            client,
            run_id,
            lambda item: (
                item["status"] == "sleeping"
                and item.get("workflow_state") is not None
                and item["workflow_state"]["interrupted"] is False
                and activity_count(item, "workflow_resumed") >= 2
            ),
            "resume after interrupt",
        )
        print("11. Resume cleared the human-review state")

        termination_reason = "Stage 2 API demo completed successfully."
        await request_json(
            client,
            "POST",
            f"/api/runs/{run_id}/terminate",
            json_body={"reason": termination_reason},
            expected_status=202,
        )
        final = await wait_for_run(
            client,
            run_id,
            lambda item: (
                item["status"] == "terminated"
                and item["completion_reason"] is not None
                and termination_reason in item["completion_reason"]
                and has_complete_stage2_trail(item)
            ),
            "graceful TERMINATED state and complete persistent activity trail",
        )
        activity_keys = [activity["activity_key"] for activity in final["activities"]]
        if len(activity_keys) != len(set(activity_keys)):
            raise RuntimeError("Persistent activity trail contains duplicate activity keys")
        print("12. Workflow terminated gracefully with a complete activity trail")

        print(f"\n=== Persistent activity timeline ({len(activity_keys)} rows) ===")
        for activity in final["activities"]:
            print(
                f"{activity['created_at']}  {activity['activity_type']:<28} {activity['summary']}"
            )
        print("\nSTAGE 2 API DEMO PASSED")


def main() -> None:
    args = parse_args()
    asyncio.run(run_demo(args.api_url))


if __name__ == "__main__":
    main()
