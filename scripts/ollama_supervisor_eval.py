"""Evaluate the local Ollama supervisor against the five required routing cases."""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.supervisor.providers import (
    OllamaSupervisorProvider,
    SupervisorProviderError,
)
from app.temporal.models import (
    BUSINESS_ACTION_NAMES,
    BusinessActionName,
    EventType,
    OrderContext,
    OrderState,
    SupervisorRequest,
    TimelineEntry,
)

REQUIRED_MODEL = "qwen3:1.7b"
RECORDED_AT = "2026-09-04T12:00:00+00:00"


class EvaluationError(RuntimeError):
    """A concise, user-actionable evaluation failure."""


@dataclass(frozen=True)
class EvaluationScenario:
    """One real-model action-routing expectation."""

    name: str
    event_type: EventType
    expected_action: BusinessActionName
    payload: dict[str, Any]


SCENARIOS = (
    EvaluationScenario(
        name="Payment failure",
        event_type=EventType.PAYMENT_FAILED,
        expected_action=BusinessActionName.MESSAGE_PAYMENTS_TEAM,
        payload={"failure_reason": "card authorization declined"},
    ),
    EvaluationScenario(
        name="Shipment delay",
        event_type=EventType.SHIPMENT_DELAYED,
        expected_action=BusinessActionName.MESSAGE_LOGISTICS_TEAM,
        payload={"delay_hours": 8, "carrier_status": "exception"},
    ),
    EvaluationScenario(
        name="Fulfillment stalled",
        event_type=EventType.NO_UPDATE_FOR_N_HOURS,
        expected_action=BusinessActionName.MESSAGE_FULFILLMENT_TEAM,
        payload={"hours_without_update": 18, "fulfillment_status": "stalled"},
    ),
    EvaluationScenario(
        name="Customer asks for an update",
        event_type=EventType.CUSTOMER_MESSAGE_RECEIVED,
        expected_action=BusinessActionName.MESSAGE_CUSTOMER,
        payload={"message": "Please tell me when my order will arrive."},
    ),
    EvaluationScenario(
        name="Unknown operational issue",
        event_type=EventType.UNKNOWN,
        expected_action=BusinessActionName.CREATE_INTERNAL_NOTE,
        payload={"issue": "Unrecognized warehouse status code X17"},
    ),
)


async def verify_ollama(client: httpx.AsyncClient, base_url: str) -> None:
    """Fail early unless local Ollama and the required model are available."""

    try:
        response = await client.get(f"{base_url.rstrip('/')}/api/tags")
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as error:
        raise EvaluationError(
            "Local Ollama is unavailable. Start Ollama, then run this evaluation again."
        ) from error

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
        raise EvaluationError("Ollama returned an unreadable model inventory.") from error

    if REQUIRED_MODEL not in installed_names:
        raise EvaluationError(
            f"Required model {REQUIRED_MODEL} is not installed. "
            f"Run `ollama pull {REQUIRED_MODEL}` and retry."
        )


def build_request(scenario: EvaluationScenario) -> SupervisorRequest:
    """Build the bounded provider request for one evaluation scenario."""

    order_state = OrderState(
        last_event_type=scenario.event_type,
        event_count=1,
        attributes={"last_event_payload": dict(scenario.payload)},
    )
    if scenario.event_type is EventType.PAYMENT_FAILED:
        order_state.payment = "failed"
    elif scenario.event_type is EventType.SHIPMENT_DELAYED:
        order_state.shipment = "delayed"

    order_id = f"ollama-eval-{scenario.event_type.value}"
    return SupervisorRequest(
        order=OrderContext(
            order_id=order_id,
            customer_id="ollama-eval-customer",
            initial_state={"source": "ollama-supervisor-eval"},
        ),
        supervisor_instructions=(
            "Keep the order moving safely and choose the primary operational action."
        ),
        trigger=f"event:{scenario.event_type.value}",
        event_type=scenario.event_type,
        next_wake_seconds=300,
        available_actions=list(BUSINESS_ACTION_NAMES),
        order_state=order_state,
        recent_activity=[
            TimelineEntry(
                sequence=1,
                recorded_at=RECORDED_AT,
                entry_type="order_event_received",
                summary=f"Received {scenario.event_type.value} for evaluation.",
                details={
                    "event_type": scenario.event_type.value,
                    "payload": dict(scenario.payload),
                },
            )
        ],
        provider="ollama",
        model=REQUIRED_MODEL,
        wake_aggressiveness="moderate",
    )


async def run_evaluation() -> None:
    """Run all scenarios and fail unless the local model scores five out of five."""

    settings = get_settings()
    total_started = time.perf_counter()
    passed = 0

    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        await verify_ollama(client, settings.ollama_base_url)
        provider = OllamaSupervisorProvider(
            base_url=settings.ollama_base_url,
            model=REQUIRED_MODEL,
            timeout_seconds=settings.ollama_timeout_seconds,
            client=client,
        )

        print("Ollama reachable: yes")
        print(f"Model: {provider.model_name}")

        for scenario in SCENARIOS:
            started = time.perf_counter()
            decision = await provider.decide(build_request(scenario))
            latency_seconds = time.perf_counter() - started
            selected_action = decision.actions[0].action_name.value if decision.actions else "none"
            scenario_passed = selected_action == scenario.expected_action.value
            passed += int(scenario_passed)
            result = "PASS" if scenario_passed else "FAIL"
            print(
                f"[{result}] {scenario.name}: selected={selected_action}; "
                f"expected={scenario.expected_action.value}; latency={latency_seconds:.3f}s"
            )

    total_seconds = time.perf_counter() - total_started
    print(f"Score: {passed}/{len(SCENARIOS)}")
    print(f"Total latency: {total_seconds:.3f}s")
    if passed != len(SCENARIOS):
        raise EvaluationError(
            f"Ollama supervisor evaluation failed with a score of {passed}/{len(SCENARIOS)}."
        )
    print("OLLAMA SUPERVISOR EVALUATION PASSED")


def main() -> int:
    """Return a shell-friendly status without exposing provider internals."""

    try:
        asyncio.run(run_evaluation())
    except (EvaluationError, SupervisorProviderError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
