"""Exercise the complete Stage 3 AI scenario through the public FastAPI API."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.config import get_settings

API_TIMEOUT_SECONDS = 15.0
DETERMINISTIC_CONVERGENCE_SECONDS = 35.0
OLLAMA_CONVERGENCE_SECONDS = 180.0
POLL_SECONDS = 0.5
DEMO_WAKE_SECONDS = 20
OLLAMA_MODEL = "qwen3:1.7b"
ALL_ACTIONS = [
    "message_fulfillment_team",
    "message_payments_team",
    "message_logistics_team",
    "message_customer",
    "create_internal_note",
]


class DemoError(RuntimeError):
    """A concise, user-actionable demo failure."""


def parse_args() -> argparse.Namespace:
    """Parse the intentionally small demo configuration surface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="FastAPI base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--provider",
        choices=("deterministic", "ollama"),
        default="ollama",
        help="Supervisor provider to demonstrate (default: ollama)",
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
    """Make one API request without including raw response bodies in failures."""

    try:
        response = await client.request(method, path, json=json_body)
    except (httpx.TimeoutException, httpx.RequestError) as error:
        raise DemoError("FastAPI is unavailable. Start the API and retry the demo.") from error
    if response.status_code != expected_status:
        raise DemoError(
            f"{method} {path} returned HTTP {response.status_code}; "
            f"expected HTTP {expected_status}."
        )
    try:
        return response.json()
    except ValueError as error:
        raise DemoError(f"{method} {path} returned unreadable JSON.") from error


async def wait_for_run(
    client: httpx.AsyncClient,
    run_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    description: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Poll the eventually consistent read model within a strict bound."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = await request_json(
            client,
            "GET",
            f"/api/runs/{run_id}",
            expected_status=200,
        )
        if predicate(run):
            return run
        await asyncio.sleep(POLL_SECONDS)
    raise DemoError(f"Timed out waiting for {description}.")


def matching_activities(
    run: dict[str, Any],
    activity_type: str,
    *,
    trigger: str | None = None,
    provider: str | None = None,
    action_name: str | None = None,
    external_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return persistent timeline rows matching compact Stage 3 evidence."""

    matches: list[dict[str, Any]] = []
    for item in run.get("activities", []):
        payload = item.get("payload") or {}
        if item.get("activity_type") != activity_type:
            continue
        if trigger is not None and payload.get("trigger") != trigger:
            continue
        if provider is not None and payload.get("provider") != provider:
            continue
        if action_name is not None and item.get("action_name") != action_name:
            continue
        if external_event_id is not None and item.get("external_event_id") != external_event_id:
            continue
        matches.append(item)
    return matches


def invocation_count(run: dict[str, Any]) -> int:
    """Read the live Workflow invocation count required by demo checkpoints."""

    state = run.get("workflow_state")
    if not isinstance(state, dict):
        raise DemoError("The live Temporal Workflow Query state is unavailable.")
    return int(state["supervisor_invocation_count"])


def instruction_is_retained(run: dict[str, Any], instruction: str) -> bool:
    """Check the exact instruction in both durable and live run state."""

    persisted = run.get("additional_instructions") or []
    workflow_state = run.get("workflow_state") or {}
    live = workflow_state.get("additional_instructions") or []
    return any(item.get("instruction") == instruction for item in persisted) and any(
        item.get("instruction") == instruction for item in live
    )


def has_compact_memory(run: dict[str, Any], *, action_name: str | None = None) -> bool:
    """Check the persisted compact-memory shape and optional executed action."""

    memory = run.get("memory_summary")
    if not isinstance(memory, dict) or not any(
        memory.get(field)
        for field in (
            "order_state",
            "important_facts",
            "open_issues",
            "actions_taken",
            "active_constraints",
            "next_review",
        )
    ):
        return False
    if action_name is None:
        return True
    return action_name in (memory.get("actions_taken") or [])


def has_complete_final_output(run: dict[str, Any]) -> bool:
    """Check all four required persisted final-output fields."""

    output = run.get("final_output")
    if not isinstance(output, dict) or not output.get("final_summary"):
        return False
    return all(
        isinstance(output.get(field), list) and bool(output[field])
        for field in ("important_actions", "key_learnings", "recommendations")
    )


async def verify_ollama() -> None:
    """Verify local Ollama reachability and the exact required model."""

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            response = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as error:
        raise DemoError("Local Ollama is unavailable. Start Ollama and retry.") from error

    try:
        models = response.json()["models"]
        installed_names = {
            name
            for model in models
            if isinstance(model, dict)
            for name in (model.get("name"), model.get("model"))
            if isinstance(name, str)
        }
    except (KeyError, TypeError, ValueError) as error:
        raise DemoError("Ollama returned an unreadable model inventory.") from error
    if OLLAMA_MODEL not in installed_names:
        raise DemoError(
            f"Required model {OLLAMA_MODEL} is missing. Run `ollama pull {OLLAMA_MODEL}`."
        )


def model_configuration(provider: str) -> dict[str, Any]:
    """Return the secret-free provider configuration stored by the API."""

    if provider == "ollama":
        return {"provider": provider, "model": OLLAMA_MODEL, "temperature": 0}
    return {"provider": provider, "temperature": 0}


async def best_effort_cleanup(client: httpx.AsyncClient, run_id: str) -> None:
    """Request graceful cleanup when the scenario exits before delivery."""

    try:
        await client.post(
            f"/api/runs/{run_id}/terminate",
            json={"reason": "Stage 3 demo cleanup after an incomplete run."},
        )
    except httpx.HTTPError:
        return


def assert_unique_action_rows(run: dict[str, Any]) -> None:
    """Prove retries did not duplicate simulated business-action evidence."""

    rows = matching_activities(run, "business_action_executed")
    keys = [row["activity_key"] for row in rows]
    if not rows or len(keys) != len(set(keys)):
        raise DemoError("Business-action rows are missing or contain duplicate keys.")
    if any(
        (row.get("payload") or {}).get("idempotency_key") != row["activity_key"] for row in rows
    ):
        raise DemoError("A business-action row has inconsistent idempotency evidence.")


def assert_instruction_reaches_scheduled_review(
    run: dict[str, Any],
    instruction: str,
    instruction_id: str,
    scheduled_key: str,
) -> None:
    """Prove a later scheduled inference received the retained instruction ID."""

    rows = run["activities"]
    instruction_indexes = [
        index
        for index, row in enumerate(rows)
        if row["activity_type"] == "instruction_added"
        and (row.get("payload") or {}).get("instruction") == instruction
    ]
    scheduled_indexes = [
        index for index, row in enumerate(rows) if row["activity_key"] == scheduled_key
    ]
    context_indexes = [
        index
        for index, row in enumerate(rows)
        if row["activity_type"] == "supervisor_invocation"
        and (row.get("payload") or {}).get("trigger") == "scheduled_wake"
        and instruction_id in ((row.get("payload") or {}).get("context_instruction_ids") or [])
    ]
    if (
        not instruction_indexes
        or not context_indexes
        or not scheduled_indexes
        or not any(
            instruction_index < context_index < scheduled_index
            for instruction_index in instruction_indexes
            for context_index in context_indexes
            for scheduled_index in scheduled_indexes
        )
    ):
        raise DemoError("The scheduled review did not receive the retained live instruction.")


def print_final_output(output: dict[str, Any]) -> None:
    """Print the four required report sections without raw provider data."""

    print("\n=== Final output ===")
    print(f"Final summary: {output['final_summary']}")
    for label, field in (
        ("Important actions", "important_actions"),
        ("Key learnings", "key_learnings"),
        ("Recommendations", "recommendations"),
    ):
        print(f"{label}:")
        for item in output[field]:
            print(f"  - {item}")


def print_timeline(run: dict[str, Any]) -> None:
    """Print concise persisted audit rows, excluding raw prompts and responses."""

    print(f"\n=== Persistent activity timeline ({len(run['activities'])} rows) ===")
    for item in run["activities"]:
        action = f" [{item['action_name']}]" if item.get("action_name") else ""
        print(
            f"{item['created_at']}  {item['activity_type']:<32} "
            f"{item['status']:<10}{action} {item['summary']}"
        )


async def run_demo(api_url: str, provider: str) -> None:
    """Run every required Stage 3 checkpoint in order."""

    if provider == "ollama":
        await verify_ollama()
        print("1. Local Ollama API is reachable")
        print(f"2. Required model is installed: {OLLAMA_MODEL}")
    else:
        print("1. Deterministic provider selected; Ollama preflight skipped")

    timeout_seconds = (
        OLLAMA_CONVERGENCE_SECONDS if provider == "ollama" else DETERMINISTIC_CONVERGENCE_SECONDS
    )
    unique = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    order_id = f"stage3-demo-{unique}"
    routine_event_id = f"stage3-payment-{unique}"
    delay_event_id = f"stage3-delay-{unique}"
    delivered_event_id = f"stage3-delivered-{unique}"
    live_instruction = "For later reviews, retain that expedited delivery is the priority."

    async with httpx.AsyncClient(
        base_url=api_url.rstrip("/"),
        timeout=API_TIMEOUT_SECONDS,
    ) as client:
        health = await request_json(client, "GET", "/health", expected_status=200)
        if health.get("status") != "healthy":
            raise DemoError("FastAPI health did not report healthy.")
        print("3. FastAPI health check passed")

        supervisor = await request_json(
            client,
            "POST",
            "/api/supervisors",
            json_body={
                "name": f"Stage 3 {provider} Supervisor {unique}",
                "base_instruction": (
                    "Monitor this order, route exceptions through allowed actions, and keep "
                    f"compact factual memory. Set next_wake_seconds to {DEMO_WAKE_SECONDS}."
                ),
                "available_actions": ALL_ACTIONS,
                "default_wake_seconds": DEMO_WAKE_SECONDS,
                "wake_aggressiveness": "moderate",
                "model_config": model_configuration(provider),
            },
            expected_status=201,
        )
        print(f"4. All-actions supervisor created with provider {provider}")

        run = await request_json(
            client,
            "POST",
            "/api/runs",
            json_body={
                "order_id": order_id,
                "supervisor_config_id": supervisor["id"],
                "order_context": {
                    "customer_id": "stage3-demo-customer",
                    "initial_state": {"source": "stage3-ai-demo", "priority": "standard"},
                },
            },
            expected_status=201,
        )
        run_id = run["id"]
        completed = False
        try:
            if run["workflow_id"] != f"order-supervisor:{order_id}":
                raise DemoError("FastAPI returned an unexpected Temporal Workflow ID.")
            print(f"5. Unique order Workflow started: {run['workflow_id']}")

            initial = await wait_for_run(
                client,
                run_id,
                lambda item: (
                    item.get("workflow_state") is not None
                    and invocation_count(item) >= 1
                    and item.get("next_wake_at") is not None
                    and bool(
                        matching_activities(
                            item,
                            "supervisor_invocation",
                            trigger="workflow_start",
                            provider=provider,
                        )
                    )
                    and bool(
                        matching_activities(
                            item,
                            "supervisor_decision",
                            trigger="workflow_start",
                            provider=provider,
                        )
                        or matching_activities(
                            item,
                            "supervisor_decision_rejected",
                            trigger="workflow_start",
                            provider=provider,
                        )
                    )
                ),
                "initial inference and its validated decision or safe fallback",
                timeout_seconds=timeout_seconds,
            )
            initial_invocations = invocation_count(initial)
            if matching_activities(
                initial,
                "supervisor_decision",
                trigger="workflow_start",
                provider=provider,
            ):
                print("6. Initial AI inference occurred; its validated decision persisted")
            else:
                print(
                    "6. Initial AI inference occurred; invalid output was rejected and the "
                    "safe fallback persisted"
                )

            await request_json(
                client,
                "POST",
                f"/api/runs/{run_id}/events",
                json_body={
                    "event_id": routine_event_id,
                    "event_type": "payment_confirmed",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": {"source": "stage3-ai-demo"},
                },
                expected_status=202,
            )
            routine = await wait_for_run(
                client,
                run_id,
                lambda item: (
                    bool(
                        matching_activities(
                            item,
                            "order_event_received",
                            external_event_id=routine_event_id,
                        )
                    )
                    and bool(
                        matching_activities(
                            item,
                            "supervisor_wake_suppressed",
                            external_event_id=routine_event_id,
                        )
                    )
                ),
                "routine payment event and suppressed wake",
                timeout_seconds=timeout_seconds,
            )
            if invocation_count(routine) != initial_invocations or matching_activities(
                routine,
                "supervisor_decision",
                trigger="event:payment_confirmed",
            ):
                raise DemoError("payment_confirmed unexpectedly invoked the supervisor.")
            print("7. payment_confirmed persisted and main-supervisor wake was suppressed")

            await request_json(
                client,
                "POST",
                f"/api/runs/{run_id}/events",
                json_body={
                    "event_id": delay_event_id,
                    "event_type": "shipment_delayed",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": {"delay_hours": 8, "carrier_status": "exception"},
                },
                expected_status=202,
            )
            important = await wait_for_run(
                client,
                run_id,
                lambda item: (
                    bool(
                        matching_activities(
                            item,
                            "supervisor_decision",
                            trigger="event:shipment_delayed",
                            provider=provider,
                        )
                    )
                    and bool(
                        matching_activities(
                            item,
                            "business_action_executed",
                            trigger="event:shipment_delayed",
                            action_name="message_logistics_team",
                        )
                    )
                    and has_compact_memory(item, action_name="message_logistics_team")
                ),
                "shipment-delay decision, logistics action, and memory update",
                timeout_seconds=timeout_seconds,
            )
            important_invocations = invocation_count(important)
            print("8. shipment_delayed woke the configured supervisor")
            print("9. message_logistics_team executed and persisted idempotently")
            print("10. Compact memory persisted the logistics action")

            scheduled_before = {
                row["activity_key"]
                for row in matching_activities(
                    important, "supervisor_decision", trigger="scheduled_wake"
                )
            }
            await request_json(
                client,
                "POST",
                f"/api/runs/{run_id}/instructions",
                json_body={"instruction": live_instruction},
                expected_status=202,
            )
            instructed = await wait_for_run(
                client,
                run_id,
                lambda item: (
                    instruction_is_retained(item, live_instruction)
                    and any(
                        (row.get("payload") or {}).get("instruction") == live_instruction
                        for row in matching_activities(item, "instruction_added")
                    )
                ),
                "live instruction retention",
                timeout_seconds=timeout_seconds,
            )
            instruction_rows = [
                row
                for row in matching_activities(instructed, "instruction_added")
                if (row.get("payload") or {}).get("instruction") == live_instruction
            ]
            instruction_id = instruction_rows[-1]["payload"]["instruction_id"]
            print("11. Live instruction persisted in PostgreSQL and Workflow state")

            scheduled = await wait_for_run(
                client,
                run_id,
                lambda item: (
                    invocation_count(item) > important_invocations
                    and instruction_is_retained(item, live_instruction)
                    and any(
                        instruction_id
                        in ((row.get("payload") or {}).get("context_instruction_ids") or [])
                        for row in matching_activities(
                            item,
                            "supervisor_invocation",
                            trigger="scheduled_wake",
                            provider=provider,
                        )
                    )
                    and bool(
                        {
                            row["activity_key"]
                            for row in matching_activities(
                                item,
                                "supervisor_decision",
                                trigger="scheduled_wake",
                                provider=provider,
                            )
                        }
                        - scheduled_before
                    )
                ),
                "a later scheduled supervisor review with retained instruction",
                timeout_seconds=timeout_seconds,
            )
            new_scheduled = [
                row
                for row in matching_activities(
                    scheduled,
                    "supervisor_decision",
                    trigger="scheduled_wake",
                    provider=provider,
                )
                if row["activity_key"] not in scheduled_before
            ]
            assert_instruction_reaches_scheduled_review(
                scheduled,
                live_instruction,
                instruction_id,
                new_scheduled[0]["activity_key"],
            )
            print("12. Scheduled AI review received the retained live-instruction context")

            await request_json(
                client,
                "POST",
                f"/api/runs/{run_id}/events",
                json_body={
                    "event_id": delivered_event_id,
                    "event_type": "delivered",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": {"delivery_status": "confirmed"},
                },
                expected_status=202,
            )
            await wait_for_run(
                client,
                run_id,
                lambda item: (
                    item.get("status") == "completed"
                    and "Order delivered" in (item.get("completion_reason") or "")
                    and has_complete_final_output(item)
                    and bool(matching_activities(item, "workflow_completion_authorized"))
                    and bool(
                        matching_activities(
                            item,
                            "final_output_generated",
                            provider=provider,
                        )
                    )
                    and bool(matching_activities(item, "workflow_completed"))
                ),
                "deterministic delivered completion and persisted final output",
                timeout_seconds=timeout_seconds,
            )
            final = await request_json(
                client,
                "GET",
                f"/api/runs/{run_id}",
                expected_status=200,
            )
            assert_unique_action_rows(final)
            activity_keys = [row["activity_key"] for row in final["activities"]]
            if len(activity_keys) != len(set(activity_keys)):
                raise DemoError("The persistent activity timeline contains duplicate keys.")
            completed = True
            print("13. delivered authorized deterministic Workflow completion")
            print("14. Validated final output persisted and was retrieved through FastAPI")
            print("15. Business-action and timeline idempotency keys are unique")

            print_final_output(final["final_output"])
            print_timeline(final)
            print("\nSTAGE 3 AI DEMO PASSED")
        finally:
            if not completed:
                await best_effort_cleanup(client, run_id)


def main() -> int:
    """Run the async demo and return a shell-friendly status."""

    args = parse_args()
    try:
        asyncio.run(run_demo(args.api_url, args.provider))
    except DemoError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError):
        print("ERROR: FastAPI returned an unexpected Stage 3 response shape.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
