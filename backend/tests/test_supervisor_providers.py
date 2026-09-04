"""Focused contract tests for deterministic and local-Ollama supervisors."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.supervisor.providers import (
    INSTRUCTION_CONTEXT_LIMIT,
    OLLAMA_NUM_CTX,
    RECENT_ACTIVITY_CONTEXT_LIMIT,
    SUPERVISOR_CONTEXT_MAX_CHARS,
    DeterministicSupervisorProvider,
    OllamaSupervisorProvider,
    SupervisorProviderUnavailable,
    SupervisorResponseInvalid,
    build_supervisor_context,
    build_supervisor_messages,
    ollama_json_schema,
)
from app.supervisor.schemas import (
    AgentDecision,
    BusinessActionProposal,
    CompactMemory,
    FinalOutput,
    decision_to_data,
    memory_to_data,
)
from app.temporal.models import (
    BUSINESS_ACTION_NAMES,
    BusinessActionName,
    CompactMemoryData,
    EventType,
    FinalOutputRequest,
    OrderContext,
    OrderState,
    RunInstruction,
    SupervisorRequest,
    TimelineEntry,
    WorkflowStatus,
)


def _supervisor_request(
    *,
    event_type: EventType | None = EventType.SHIPMENT_DELAYED,
    memory_summary: CompactMemoryData | None = None,
    recent_activity: list[TimelineEntry] | None = None,
    additional_instructions: list[RunInstruction] | None = None,
) -> SupervisorRequest:
    return SupervisorRequest(
        order=OrderContext(
            order_id="provider-test-order",
            customer_id="provider-test-customer",
            initial_state={"priority": "standard"},
        ),
        supervisor_instructions="Keep the order moving safely and document uncertainty.",
        trigger=f"event:{event_type.value}" if event_type else "scheduled_wake",
        event_type=event_type,
        next_wake_seconds=120,
        additional_instructions=list(additional_instructions or []),
        available_actions=list(BUSINESS_ACTION_NAMES),
        order_state=OrderState(
            lifecycle="open",
            payment="confirmed",
            shipment="delayed" if event_type is EventType.SHIPMENT_DELAYED else "created",
            last_event_type=event_type,
            event_count=3,
            attributes={"carrier": "Test Carrier"},
        ),
        memory_summary=memory_summary,
        recent_activity=list(recent_activity or []),
        provider="deterministic",
        model=None,
        wake_aggressiveness="moderate",
    )


def _valid_decision_payload() -> dict[str, Any]:
    return {
        "decision_summary": "The shipment delay needs logistics follow-up.",
        "urgency": "high",
        "should_act": True,
        "actions": [
            {
                "action_name": "message_logistics_team",
                "arguments": {"content": "Please investigate the delayed shipment for this order."},
            }
        ],
        "memory_update": {
            "order_state": "Payment is confirmed and the shipment is delayed.",
            "important_facts": ["The carrier reported a shipment delay."],
            "open_issues": ["Logistics follow-up is outstanding."],
            "actions_taken": [],
            "active_constraints": ["Prefer a concise customer-safe update."],
            "next_review": "Review again in 120 seconds.",
        },
        "next_wake_seconds": 120,
        "completion_recommendation": False,
    }


def _final_output_request() -> FinalOutputRequest:
    return FinalOutputRequest(
        order=OrderContext(order_id="provider-test-order"),
        order_state=OrderState(
            lifecycle="completed",
            payment="confirmed",
            shipment="delivered",
            event_count=4,
        ),
        supervisor_instructions="Keep the final report concise.",
        completion_status=WorkflowStatus.COMPLETED,
        completion_reason="Order delivered (event_id=provider-test-delivered).",
        memory_summary=CompactMemoryData(
            order_state="The order was delivered.",
            important_facts=["Delivery was confirmed."],
        ),
        recent_activity=[
            TimelineEntry(
                sequence=9,
                recorded_at="2026-09-04T12:00:00+00:00",
                entry_type="business_action_executed",
                summary="Messaged the logistics team.",
                details={"action_name": "message_logistics_team"},
            )
        ],
        provider="ollama",
        model="qwen3:1.7b",
    )


@pytest.mark.asyncio
async def test_deterministic_provider_returns_valid_shared_decision_schema() -> None:
    provider = DeterministicSupervisorProvider()
    request = _supervisor_request(event_type=EventType.PAYMENT_FAILED)

    decision = await provider.decide(request)
    validated = AgentDecision.model_validate(decision.model_dump(mode="json"))
    temporal_data = decision_to_data(
        validated,
        trigger=request.trigger,
        provider=provider.name,
        model=provider.model_name,
    )

    assert validated.should_act is True
    assert validated.actions[0].action_name is BusinessActionName.MESSAGE_PAYMENTS_TEAM
    assert validated.next_wake_seconds == request.next_wake_seconds
    assert temporal_data.provider == "deterministic"
    assert temporal_data.model == "deterministic-rules-v1"
    assert temporal_data.proposed_action_names == ["message_payments_team"]


@pytest.mark.parametrize(
    ("mutate", "expected_location"),
    [
        (
            lambda payload: payload["actions"][0].update(action_name="close_workflow"),
            ("actions", 0, "action_name"),
        ),
        (
            lambda payload: payload["actions"][0]["arguments"].update(channel="arbitrary"),
            ("actions", 0, "arguments", "channel"),
        ),
        (
            lambda payload: payload["actions"][0].update(arguments={}),
            ("actions", 0, "arguments", "content"),
        ),
        (
            lambda payload: payload["actions"][0]["arguments"].update(
                content="Terminate workflow immediately."
            ),
            ("actions", 0, "arguments", "content"),
        ),
    ],
    ids=("unknown-action", "extra-argument", "missing-content", "lifecycle-bypass"),
)
def test_decision_schema_rejects_unknown_or_malformed_actions(
    mutate,
    expected_location: tuple[object, ...],
) -> None:
    payload = deepcopy(_valid_decision_payload())
    mutate(payload)

    with pytest.raises(ValidationError) as raised:
        AgentDecision.model_validate(payload)

    assert expected_location in {tuple(error["loc"]) for error in raised.value.errors()}


@pytest.mark.parametrize("wake_seconds", [0, 86_401])
def test_decision_schema_rejects_out_of_range_wake_seconds(wake_seconds: int) -> None:
    payload = _valid_decision_payload()
    payload["next_wake_seconds"] = wake_seconds

    with pytest.raises(ValidationError) as raised:
        AgentDecision.model_validate(payload)

    assert ("next_wake_seconds",) in {tuple(error["loc"]) for error in raised.value.errors()}


def test_decision_schema_rejects_action_flag_mismatch() -> None:
    payload = _valid_decision_payload()
    payload["should_act"] = False

    with pytest.raises(ValidationError, match="should_act must match"):
        AgentDecision.model_validate(payload)


def test_decision_schema_requires_explicit_actions_list() -> None:
    payload = _valid_decision_payload()
    del payload["actions"]

    with pytest.raises(ValidationError) as raised:
        AgentDecision.model_validate(payload)

    assert ("actions",) in {tuple(error["loc"]) for error in raised.value.errors()}


def test_supervisor_context_bounds_history_and_retains_recent_instructions() -> None:
    recent_activity = [
        TimelineEntry(
            sequence=sequence,
            recorded_at=f"2026-09-04T12:{sequence:02d}:00+00:00",
            entry_type="test_entry",
            summary=f"Timeline item {sequence} " + ("x" * 700),
            details={"sequence": sequence},
        )
        for sequence in range(1, 15)
    ]
    instructions = [
        RunInstruction(
            instruction_id=f"instruction-{index}",
            instruction=f"Operator instruction {index}",
            created_at=f"2026-09-04T13:{index:02d}:00+00:00",
        )
        for index in range(1, 12)
    ]
    memory = CompactMemoryData(
        order_state="The order is open.",
        important_facts=["Existing compact fact survives prompt construction."],
        open_issues=["Existing issue remains open."],
        actions_taken=["Created an earlier internal note."],
        active_constraints=["Do not issue a refund automatically."],
        next_review="Await the next carrier update.",
    )

    context = build_supervisor_context(
        _supervisor_request(
            memory_summary=memory,
            recent_activity=recent_activity,
            additional_instructions=instructions,
        )
    )

    retained_sequences = [item["sequence"] for item in context["recent_activity"]]
    assert 1 <= len(retained_sequences) <= RECENT_ACTIVITY_CONTEXT_LIMIT
    assert retained_sequences == list(range(15 - len(retained_sequences), 15))
    assert all(len(item["summary"]) <= 500 for item in context["recent_activity"])
    assert len(context["run_instructions"]) == INSTRUCTION_CONTEXT_LIMIT
    assert [item["instruction_id"] for item in context["run_instructions"]] == [
        f"instruction-{index}" for index in range(4, 12)
    ]
    assert context["omitted_instruction_count"] == 3
    assert context["compact_memory"] == CompactMemory.model_validate(memory.__dict__).model_dump(
        mode="json"
    )


@pytest.mark.asyncio
async def test_compact_memory_survives_future_deterministic_invocations() -> None:
    provider = DeterministicSupervisorProvider()
    initial_memory = CompactMemoryData(
        order_state="The order is open.",
        important_facts=["This customer needs careful handling."],
        open_issues=["An earlier issue remains under review."],
        actions_taken=["Created an internal note."],
        active_constraints=["Do not refund automatically."],
        next_review="Review after the next event.",
    )
    instruction = RunInstruction(
        instruction_id="preserved-instruction",
        instruction="Prioritize speed over cost.",
        created_at="2026-09-04T14:00:00+00:00",
    )
    first_request = _supervisor_request(
        event_type=EventType.PAYMENT_FAILED,
        memory_summary=initial_memory,
        additional_instructions=[instruction],
    )

    first = await provider.decide(first_request)
    second = await provider.decide(
        replace(
            first_request,
            trigger="event:shipment_delayed",
            event_type=EventType.SHIPMENT_DELAYED,
            memory_summary=memory_to_data(first.memory_update),
        )
    )

    assert "This customer needs careful handling." in second.memory_update.important_facts
    assert "Latest important event: payment_failed." in second.memory_update.important_facts
    assert "Latest important event: shipment_delayed." in second.memory_update.important_facts
    assert "Created an internal note." in second.memory_update.actions_taken
    assert "Do not refund automatically." in second.memory_update.active_constraints
    assert "Operator: Prioritize speed over cost." in second.memory_update.active_constraints


def test_complete_supervisor_context_has_a_hard_serialized_budget() -> None:
    recent_activity = [
        TimelineEntry(
            sequence=index,
            recorded_at="2026-09-04T12:00:00+00:00",
            entry_type="large_test_entry",
            summary="s" * 500,
            details={f"field-{field}": "x" * 500 for field in range(12)},
        )
        for index in range(20)
    ]
    instructions = [
        RunInstruction(
            instruction_id=f"bounded-instruction-{index}",
            instruction="i" * 500,
            created_at="2026-09-04T12:00:00+00:00",
        )
        for index in range(12)
    ]
    memory = CompactMemoryData(
        order_state="o" * 500,
        important_facts=["f" * 300] * 6,
        open_issues=["i" * 300] * 6,
        actions_taken=["a" * 300] * 6,
        active_constraints=["c" * 300] * 6,
        next_review="n" * 300,
    )
    request = _supervisor_request(
        memory_summary=memory,
        recent_activity=recent_activity,
        additional_instructions=instructions,
    )
    request.supervisor_instructions = "🧠" * 1_200
    request.order.initial_state = {f"field-{index}": "🌍" * 500 for index in range(12)}
    request.order_state.attributes = {f"field-{index}": "📦" * 500 for index in range(12)}

    context = build_supervisor_context(request)
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    messages = build_supervisor_messages(request)

    assert len(encoded) <= SUPERVISOR_CONTEXT_MAX_CHARS
    assert len(messages[1]["content"]) <= SUPERVISOR_CONTEXT_MAX_CHARS
    assert context["run_instructions"][-1]["instruction_id"] == "bounded-instruction-11"
    assert len(context["recent_activity"]) <= RECENT_ACTIVITY_CONTEXT_LIMIT


def test_workflow_start_prompt_has_an_explicit_safe_output_shape() -> None:
    request = _supervisor_request()
    event_system_message = build_supervisor_messages(request)[0]["content"]
    request.trigger = "workflow_start"
    request.event_type = None
    request.order_state = OrderState()
    request.recent_activity = [
        TimelineEntry(
            sequence=1,
            recorded_at="2026-09-04T12:00:00+00:00",
            entry_type="workflow_started",
            summary="Started order supervision.",
        )
    ]

    messages = build_supervisor_messages(request)
    system_message = messages[0]["content"]
    user_context = json.loads(messages[1]["content"])

    assert "STARTUP RULE: When trigger is exactly workflow_start" in system_message
    assert "Return urgency low, should_act false, actions []" in system_message
    assert (
        "set important_facts, open_issues, actions_taken, and active_constraints to []"
        in system_message
    )
    assert "completion_recommendation false" in system_message
    assert "configured default next_wake_seconds" in system_message
    assert "do not infer or propose payment, fulfillment, logistics" in system_message
    assert "return should_act false and actions []" in event_system_message
    assert "STARTUP RULE" not in event_system_message
    assert user_context["recent_activity"] == []
    assert request.recent_activity[0].entry_type == "workflow_started"


@pytest.mark.asyncio
async def test_deterministic_final_output_uses_authoritative_compact_action_history() -> None:
    provider = DeterministicSupervisorProvider()
    request = _final_output_request()
    request.memory_summary = CompactMemoryData(
        order_state="The order was delivered.",
        actions_taken=["message_payments_team"],
    )

    output = await provider.finalize(request)

    assert FinalOutput.model_validate(output.model_dump(mode="json")) == output
    assert output.important_actions == [
        "Executed simulated action message_payments_team.",
        "Executed simulated action message_logistics_team.",
    ]


@pytest.mark.asyncio
async def test_ollama_decision_request_uses_bounded_settings_and_parses_schema() -> None:
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://ollama.test:11434/api/chat")
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(_valid_decision_payload())}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaSupervisorProvider(
            base_url="http://ollama.test:11434/",
            model="qwen3:1.7b",
            timeout_seconds=7.5,
            client=client,
        )
        decision = await provider.decide(_supervisor_request())

    assert decision.actions[0].action_name is BusinessActionName.MESSAGE_LOGISTICS_TEAM
    assert captured_body["model"] == "qwen3:1.7b"
    assert captured_body["stream"] is False
    assert captured_body["think"] is False
    assert captured_body["keep_alive"] == 0
    assert captured_body["options"] == {
        "temperature": 0,
        "num_ctx": OLLAMA_NUM_CTX,
        "num_predict": 512,
    }
    assert captured_body["format"] == ollama_json_schema(AgentDecision.model_json_schema())
    assert "maximum" not in captured_body["format"]["properties"]["next_wake_seconds"]
    assert captured_body["messages"][0]["role"] == "system"
    assert captured_body["messages"][1]["role"] == "user"
    assert "close, terminate, complete" in captured_body["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_payload",
    [
        {"message": {"content": "not valid JSON"}},
        {"message": {"content": json.dumps({"decision_summary": "Incomplete"})}},
        {"message": {"content": json.dumps(["not", "an", "object"])}},
        {"unexpected": "shape"},
    ],
    ids=("invalid-json", "schema-invalid", "non-object", "missing-content"),
)
async def test_ollama_rejects_malformed_responses(
    response_payload: dict[str, Any],
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=response_payload))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaSupervisorProvider(
            base_url="http://ollama.test:11434",
            model="qwen3:1.7b",
            timeout_seconds=1,
            client=client,
        )
        with pytest.raises(SupervisorResponseInvalid, match="^Ollama returned"):
            await provider.decide(_supervisor_request())


@pytest.mark.asyncio
async def test_ollama_timeout_is_sanitized_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("local socket details must stay hidden", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaSupervisorProvider(
            base_url="http://ollama.test:11434",
            model="qwen3:1.7b",
            timeout_seconds=0.1,
            client=client,
        )
        with pytest.raises(
            SupervisorProviderUnavailable,
            match=r"^The local Ollama supervisor is unavailable\.$",
        ) as raised:
            await provider.decide(_supervisor_request())

    assert "socket" not in str(raised.value)


@pytest.mark.asyncio
async def test_ollama_http_failure_is_sanitized_as_unavailable() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503, text="daemon internals"))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaSupervisorProvider(
            base_url="http://ollama.test:11434",
            model="qwen3:1.7b",
            timeout_seconds=1,
            client=client,
        )
        with pytest.raises(SupervisorProviderUnavailable) as raised:
            await provider.decide(_supervisor_request())

    assert str(raised.value) == "The local Ollama supervisor is unavailable."
    assert "daemon internals" not in str(raised.value)


@pytest.mark.asyncio
async def test_ollama_final_output_uses_final_schema_and_parses_result() -> None:
    final_payload = {
        "final_summary": "The order was delivered after logistics follow-up.",
        "important_actions": ["The logistics team investigated the delay."],
        "key_learnings": ["The delayed shipment needed an explicit review."],
        "recommendations": ["Retain the carrier timeline for audit."],
    }
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": final_payload}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaSupervisorProvider(
            base_url="http://ollama.test:11434",
            model="qwen3:1.7b",
            timeout_seconds=1,
            client=client,
        )
        output = await provider.finalize(_final_output_request())

    assert output == FinalOutput.model_validate(final_payload)
    assert captured_body["format"] == ollama_json_schema(FinalOutput.model_json_schema())
    assert captured_body["stream"] is False
    assert captured_body["think"] is False


def test_business_action_proposal_rejects_extra_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        BusinessActionProposal.model_validate(
            {
                "action_name": "create_internal_note",
                "arguments": {"content": "Document the unknown issue."},
                "callable": "arbitrary.module.function",
            }
        )
