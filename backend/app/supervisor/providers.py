"""Small deterministic and local-Ollama supervisor provider implementations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.supervisor.schemas import (
    AgentDecision,
    BusinessActionArguments,
    BusinessActionProposal,
    CompactMemory,
    DecisionUrgency,
    FinalOutput,
    memory_from_data,
)
from app.temporal.models import (
    BusinessActionName,
    EventType,
    FinalOutputRequest,
    SupervisorRequest,
    TimelineEntry,
)

RECENT_ACTIVITY_CONTEXT_LIMIT = 10
INSTRUCTION_CONTEXT_LIMIT = 8
MAX_CONTEXT_STRING_LENGTH = 500
SUPERVISOR_CONTEXT_MAX_CHARS = 4_000
OLLAMA_NUM_CTX = 2048
OLLAMA_NUM_PREDICT = 512
OLLAMA_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}
)


class SupervisorProviderError(RuntimeError):
    """Base class for sanitized provider failures."""


class SupervisorProviderUnavailable(SupervisorProviderError):
    """The configured provider could not be reached within its bound."""


class SupervisorResponseInvalid(SupervisorProviderError):
    """The provider returned data that failed the application contract."""


class SupervisorProvider(Protocol):
    """The complete provider surface needed by Temporal Activities."""

    name: str
    model_name: str

    async def decide(self, request: SupervisorRequest) -> AgentDecision: ...

    async def finalize(self, request: FinalOutputRequest) -> FinalOutput: ...


class DeterministicSupervisorProvider:
    """Predictable provider used by tests and local fallback operation."""

    name = "deterministic"
    model_name = "deterministic-rules-v1"

    async def decide(self, request: SupervisorRequest) -> AgentDecision:
        memory = memory_from_data(request.memory_summary).model_copy(deep=True)
        action_name = _action_for_event(request.event_type)
        actions: list[BusinessActionProposal] = []
        if action_name is not None:
            actions.append(
                BusinessActionProposal(
                    action_name=action_name,
                    arguments=BusinessActionArguments(
                        content=_deterministic_action_content(action_name, request)
                    ),
                )
            )

        summary, urgency = _deterministic_summary(request, action_name)
        memory.order_state = _order_state_sentence(request)
        memory.important_facts = _append_bounded(
            memory.important_facts,
            _fact_for_event(request.event_type),
        )
        memory.open_issues = _updated_open_issues(memory.open_issues, request.event_type)
        memory.active_constraints = _append_instructions(
            memory.active_constraints,
            request,
        )
        memory.next_review = f"Review again in {request.next_wake_seconds} seconds."

        return AgentDecision(
            decision_summary=summary,
            urgency=urgency,
            should_act=bool(actions),
            actions=actions,
            memory_update=memory,
            next_wake_seconds=request.next_wake_seconds,
            completion_recommendation=False,
        )

    async def finalize(self, request: FinalOutputRequest) -> FinalOutput:
        action_names = list(
            memory_from_data(request.memory_summary).actions_taken
            if request.memory_summary is not None
            else []
        )
        for action_name in _executed_action_names(request.recent_activity):
            if action_name not in action_names:
                action_names.append(action_name)
        important_actions = (
            [f"Executed simulated action {name}." for name in action_names]
            if action_names
            else ["No simulated business action was required before the run ended."]
        )
        status_word = request.completion_status.value
        return FinalOutput(
            final_summary=(
                f"Order {request.order.order_id} finished with status {status_word}. "
                f"{request.completion_reason}"
            ),
            important_actions=important_actions,
            key_learnings=[
                f"The Workflow processed {request.order_state.event_count} unique order events.",
                "Temporal lifecycle rules, not an AI recommendation, authorized completion.",
            ],
            recommendations=[
                "Review the persistent activity timeline before closing operational follow-up."
            ],
        )


class OllamaSupervisorProvider:
    """Direct, resource-conscious client for Ollama's local chat endpoint."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def decide(self, request: SupervisorRequest) -> AgentDecision:
        raw = await self._chat(
            schema=ollama_json_schema(AgentDecision.model_json_schema()),
            messages=build_supervisor_messages(request),
        )
        try:
            return AgentDecision.model_validate(raw)
        except ValidationError as error:
            raise SupervisorResponseInvalid(
                "Ollama returned an invalid supervisor decision."
            ) from error

    async def finalize(self, request: FinalOutputRequest) -> FinalOutput:
        raw = await self._chat(
            schema=ollama_json_schema(FinalOutput.model_json_schema()),
            messages=build_final_output_messages(request),
        )
        try:
            return FinalOutput.model_validate(raw)
        except ValidationError as error:
            raise SupervisorResponseInvalid("Ollama returned an invalid final output.") from error

    async def _chat(
        self,
        *,
        schema: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> Mapping[str, Any]:
        body = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "format": schema,
            "options": {
                "temperature": 0,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": OLLAMA_NUM_PREDICT,
            },
        }
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(f"{self.base_url}/api/chat", json=body)
            else:
                response = await self._client.post(f"{self.base_url}/api/chat", json=body)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as error:
            raise SupervisorProviderUnavailable(
                "The local Ollama supervisor is unavailable."
            ) from error

        try:
            payload = response.json()
            content = payload["message"]["content"]
            if isinstance(content, str):
                parsed = json.loads(content)
            elif isinstance(content, Mapping):
                parsed = dict(content)
            else:
                raise TypeError("unexpected Ollama content type")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SupervisorResponseInvalid(
                "Ollama returned an unreadable structured response."
            ) from error
        if not isinstance(parsed, Mapping):
            raise SupervisorResponseInvalid("Ollama returned a non-object structured response.")
        return parsed


def build_provider(
    provider_name: str,
    *,
    model: str | None,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> SupervisorProvider:
    """Build exactly one of the two supported providers outside Workflow code."""

    normalized = normalize_provider_name(provider_name)
    if normalized == "deterministic":
        return DeterministicSupervisorProvider()
    return OllamaSupervisorProvider(
        base_url=settings.ollama_base_url,
        model=model or settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        client=client,
    )


def normalize_provider_name(value: str | None, *, default: str = "deterministic") -> str:
    """Resolve the legacy Stage 2 sentinel without creating a third provider."""

    candidate = (value or default).strip().lower()
    if candidate == "none":
        candidate = "deterministic"
    if candidate not in {"deterministic", "ollama"}:
        raise ValueError("provider must be deterministic or ollama")
    return candidate


def ollama_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep Pydantic's structure while omitting bounds Ollama cannot compile.

    The application still applies the complete Pydantic model, including every
    omitted bound, after generation. This compatibility projection preserves
    object shapes, required fields, enums, and ``additionalProperties`` rules.
    """

    def project(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: project(item)
                for key, item in value.items()
                if key not in OLLAMA_UNSUPPORTED_SCHEMA_KEYWORDS
            }
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return project(schema)


def build_supervisor_context(request: SupervisorRequest) -> dict[str, Any]:
    """Build the small bounded context sent to the local model."""

    memory = memory_from_data(request.memory_summary)
    recent = request.recent_activity[-RECENT_ACTIVITY_CONTEXT_LIMIT:]
    instructions = request.additional_instructions[-INSTRUCTION_CONTEXT_LIMIT:]
    context = {
        "supervisor_base_instruction": _bounded_text(request.supervisor_instructions, 700),
        "order_context": {
            "order_id": _bounded_text(request.order.order_id, 128),
            "customer_id": _bounded_text(request.order.customer_id or "", 200) or None,
            "initial_state": _bounded_component(request.order.initial_state, max_chars=400),
        },
        "current_order_state": {
            "lifecycle": _bounded_text(request.order_state.lifecycle, 100),
            "payment": _bounded_text(request.order_state.payment, 100),
            "shipment": _bounded_text(request.order_state.shipment, 100),
            "refund": _bounded_text(request.order_state.refund, 100),
            "last_event_type": request.order_state.last_event_type.value
            if request.order_state.last_event_type
            else None,
            "event_count": request.order_state.event_count,
            "attributes": _bounded_component(
                request.order_state.attributes,
                max_chars=400,
            ),
        },
        "trigger": _bounded_text(request.trigger, 160),
        "event_type": request.event_type.value if request.event_type else None,
        "compact_memory": _memory_context(memory),
        "recent_activity": [_timeline_payload(entry) for entry in recent],
        "run_instructions": [
            {
                "instruction_id": instruction.instruction_id,
                "instruction": _bounded_text(instruction.instruction, 160),
            }
            for instruction in instructions
        ],
        "omitted_instruction_count": max(
            0, len(request.additional_instructions) - len(instructions)
        ),
        "allowed_actions": [
            _bounded_text(str(action_name), 50) for action_name in request.available_actions[:5]
        ],
        "wake_guidance": {
            "default_next_wake_seconds": request.next_wake_seconds,
            "wake_aggressiveness": request.wake_aggressiveness,
            "minimum_seconds": 1,
            "maximum_seconds": 86_400,
        },
    }
    return _fit_supervisor_context(context)


def build_supervisor_messages(request: SupervisorRequest) -> list[dict[str, str]]:
    """Return concise messages with explicit lifecycle and action boundaries."""

    context = build_supervisor_context(request)
    if request.trigger == "workflow_start":
        context["recent_activity"] = [
            entry
            for entry in context["recent_activity"]
            if entry["entry_type"] != "workflow_started"
        ]

    system = "\n".join(
        [
            "You supervise one commerce order. Return only the JSON object required by the "
            "supplied schema. Keep decision_summary concise and compact memory factual.",
            "Allowed action meanings:",
            "- message_fulfillment_team: ask fulfillment staff to investigate progress.",
            "- message_payments_team: ask payments staff to investigate payment or refund.",
            "- message_logistics_team: ask logistics staff to investigate shipment or carrier.",
            "- message_customer: send a simulated order update to the customer.",
            "- create_internal_note: safely document an unknown or ambiguous issue.",
            "Choose only actions listed in allowed_actions. Actions are proposals validated by "
            "application code. Use exactly one content argument and no extra arguments.",
            "Never bypass lifecycle rules or close, terminate, complete, pause, or resume the "
            "Workflow. You may recommend completion, but only Temporal can authorize it.",
            "Match should_act to whether actions is non-empty. Keep next wake within the supplied "
            "bounds and appropriate to the situation.",
            "Stay within a strict response budget: propose at most one primary action; keep the "
            "summary and action content to one short sentence; use at most one short item per "
            "memory list; do not repeat order or customer IDs; keep next_review brief.",
            "At scheduled_wake without a new explicit exception, pending payment and a "
            "not-created shipment are normal: return should_act false and actions []. Do not "
            "repeat an action already in "
            "compact_memory.actions_taken unless the current event is a new explicit failure. "
            "actions_taken describes prior executed actions only; do not add current proposals "
            "to it.",
            "Routing: payment failure -> message_payments_team; shipment delay -> "
            "message_logistics_team; stalled fulfillment/no update -> "
            "message_fulfillment_team; direct customer update request -> message_customer; "
            "unknown issue -> create_internal_note.",
            *(
                [
                    "STARTUP RULE: When trigger is exactly workflow_start, event_type is null, "
                    "the state is open/pending/not_created/not_requested, and initial_state "
                    "contains no explicit exception, initialization is not a failure. Return "
                    "urgency low, should_act false, actions [], completion_recommendation false, "
                    "and the configured default next_wake_seconds.",
                    "For that ordinary startup response, memory_update must use one short "
                    "order_state sentence; set important_facts, open_issues, actions_taken, and "
                    "active_constraints to []; keep next_review to one short sentence.",
                    "At that ordinary startup, do not infer or propose payment, fulfillment, "
                    "logistics, customer, or internal-note work merely from pending payment or "
                    "a not-created shipment.",
                ]
                if request.trigger == "workflow_start"
                else []
            ),
        ]
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def build_final_output_messages(request: FinalOutputRequest) -> list[dict[str, str]]:
    """Return a bounded final-report prompt after deterministic authorization."""

    context = {
        "supervisor_base_instruction": _bounded_text(
            request.supervisor_instructions,
            700,
        ),
        "order_id": request.order.order_id,
        "completion_status": request.completion_status.value,
        "completion_reason": _bounded_text(request.completion_reason),
        "order_state": _bounded_value(
            {
                "lifecycle": request.order_state.lifecycle,
                "payment": request.order_state.payment,
                "shipment": request.order_state.shipment,
                "refund": request.order_state.refund,
                "event_count": request.order_state.event_count,
            }
        ),
        "compact_memory": _memory_context(memory_from_data(request.memory_summary)),
        "recent_activity": [
            _timeline_payload(entry)
            for entry in request.recent_activity[-RECENT_ACTIVITY_CONTEXT_LIMIT:]
        ],
        "run_instructions": [
            _bounded_text(item.instruction, 160)
            for item in request.additional_instructions[-INSTRUCTION_CONTEXT_LIMIT:]
        ],
    }
    context = _fit_final_output_context(context)
    system = " ".join(
        [
            "Generate the concise end-of-run report required by the JSON schema.",
            "The Workflow already authorized the terminal state; do not change lifecycle status.",
            "Use only supplied facts. Return a non-empty final_summary and at least one concise",
            "item in important_actions, key_learnings, and recommendations.",
            "Stay within a strict response budget: use one short sentence and one short item in",
            "each list unless another item is essential.",
            "Do not include hidden reasoning.",
        ]
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _action_for_event(event_type: EventType | None) -> BusinessActionName | None:
    return {
        EventType.PAYMENT_FAILED: BusinessActionName.MESSAGE_PAYMENTS_TEAM,
        EventType.SHIPMENT_DELAYED: BusinessActionName.MESSAGE_LOGISTICS_TEAM,
        EventType.REFUND_REQUESTED: BusinessActionName.MESSAGE_PAYMENTS_TEAM,
        EventType.CUSTOMER_MESSAGE_RECEIVED: BusinessActionName.MESSAGE_CUSTOMER,
        EventType.NO_UPDATE_FOR_N_HOURS: BusinessActionName.MESSAGE_FULFILLMENT_TEAM,
        EventType.UNKNOWN: BusinessActionName.CREATE_INTERNAL_NOTE,
    }.get(event_type)


def _deterministic_action_content(
    action_name: BusinessActionName,
    request: SupervisorRequest,
) -> str:
    event_name = request.event_type.value if request.event_type else "scheduled review"
    return {
        BusinessActionName.MESSAGE_FULFILLMENT_TEAM: (
            f"Please investigate fulfillment progress for order {request.order.order_id} "
            f"after {event_name}."
        ),
        BusinessActionName.MESSAGE_PAYMENTS_TEAM: (
            f"Please investigate the payment state for order {request.order.order_id} "
            f"after {event_name}."
        ),
        BusinessActionName.MESSAGE_LOGISTICS_TEAM: (
            f"Please investigate shipment status for order {request.order.order_id} "
            f"after {event_name}."
        ),
        BusinessActionName.MESSAGE_CUSTOMER: (
            f"Order {request.order.order_id} is being reviewed; we will share another update soon."
        ),
        BusinessActionName.CREATE_INTERNAL_NOTE: (
            f"Review unknown issue for order {request.order.order_id} triggered by {event_name}."
        ),
    }[action_name]


def _deterministic_summary(
    request: SupervisorRequest,
    action_name: BusinessActionName | None,
) -> tuple[str, DecisionUrgency]:
    if request.trigger == "workflow_start":
        return "Initial order review completed; no immediate action is needed.", DecisionUrgency.LOW
    if request.trigger == "scheduled_wake":
        return (
            "Scheduled order review completed; no external action is needed.",
            DecisionUrgency.LOW,
        )
    if action_name is not None and request.event_type is not None:
        return (
            f"Important {request.event_type.value} event requires supervisor attention.",
            DecisionUrgency.HIGH,
        )
    return "Event reviewed; no immediate action is needed.", DecisionUrgency.LOW


def _order_state_sentence(request: SupervisorRequest) -> str:
    state = request.order_state
    return (
        f"Lifecycle {state.lifecycle}; payment {state.payment}; shipment {state.shipment}; "
        f"refund {state.refund}."
    )


def _fact_for_event(event_type: EventType | None) -> str | None:
    if event_type is None:
        return None
    return f"Latest important event: {event_type.value}."


def _updated_open_issues(items: list[str], event_type: EventType | None) -> list[str]:
    issues = list(items)
    issue = {
        EventType.PAYMENT_FAILED: "Payment failure requires investigation.",
        EventType.SHIPMENT_DELAYED: "Shipment delay requires logistics follow-up.",
        EventType.REFUND_REQUESTED: "Refund request requires payments review.",
        EventType.CUSTOMER_MESSAGE_RECEIVED: "Customer is waiting for an update.",
        EventType.NO_UPDATE_FOR_N_HOURS: "Fulfillment progress appears stalled.",
        EventType.UNKNOWN: "Unknown operational issue requires internal review.",
    }.get(event_type)
    return _append_bounded(issues, issue)


def _append_instructions(items: list[str], request: SupervisorRequest) -> list[str]:
    result = list(items)
    for instruction in request.additional_instructions[-INSTRUCTION_CONTEXT_LIMIT:]:
        result = _append_bounded(result, f"Operator: {instruction.instruction}")
    return result


def _append_bounded(items: list[str], value: str | None) -> list[str]:
    result = list(items)
    if value and value not in result:
        result.append(_bounded_text(value, 300))
    return result[-6:]


def _executed_action_names(entries: list[TimelineEntry]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        if entry.entry_type != "business_action_executed":
            continue
        name = entry.details.get("action_name")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def _timeline_payload(entry: TimelineEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "recorded_at": entry.recorded_at,
        "entry_type": entry.entry_type,
        "summary": _bounded_text(entry.summary, 160),
        "details": _bounded_component(entry.details, max_chars=240),
    }


def _memory_context(memory: CompactMemory) -> dict[str, Any]:
    return {
        "order_state": _bounded_text(memory.order_state, 240),
        "important_facts": [_bounded_text(item, 120) for item in memory.important_facts[-4:]],
        "open_issues": [_bounded_text(item, 120) for item in memory.open_issues[-4:]],
        "actions_taken": [_bounded_text(item, 120) for item in memory.actions_taken[-4:]],
        "active_constraints": [_bounded_text(item, 120) for item in memory.active_constraints[-4:]],
        "next_review": _bounded_text(memory.next_review, 160),
    }


def _bounded_component(value: Any, *, max_chars: int) -> Any:
    projected = _bounded_value(value)
    if len(json.dumps(projected, ensure_ascii=False, separators=(",", ":"))) <= max_chars:
        return projected
    return {"omitted": "Value exceeded its context budget."}


def _fit_supervisor_context(context: dict[str, Any]) -> dict[str, Any]:
    """Prune oldest optional detail until the complete user context fits 2k tokens."""

    def encoded_size() -> int:
        return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))

    recent = context["recent_activity"]
    while encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS and len(recent) > 3:
        recent.pop(0)

    instructions = context["run_instructions"]
    while encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS and len(instructions) > 2:
        instructions.pop(0)
        context["omitted_instruction_count"] += 1

    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        for entry in recent:
            entry["details"] = {}
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        context["order_context"]["initial_state"] = {}
        context["current_order_state"]["attributes"] = {}
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        for field in (
            "important_facts",
            "open_issues",
            "actions_taken",
            "active_constraints",
        ):
            context["compact_memory"][field] = context["compact_memory"][field][-1:]

    if encoded_size() <= SUPERVISOR_CONTEXT_MAX_CHARS:
        return context

    # Defensive last resort for direct Workflow starts with unusually large strings.
    context["supervisor_base_instruction"] = _bounded_text(
        context["supervisor_base_instruction"], 240
    )
    context["recent_activity"] = recent[-1:]
    context["run_instructions"] = instructions[-1:]
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        context["compact_memory"] = {
            "order_state": _bounded_text(context["compact_memory"]["order_state"], 120),
            "important_facts": [],
            "open_issues": [],
            "actions_taken": context["compact_memory"]["actions_taken"][-1:],
            "active_constraints": [],
            "next_review": _bounded_text(context["compact_memory"]["next_review"], 80),
        }
        context["recent_activity"] = []
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        raise SupervisorResponseInvalid("The bounded supervisor context is too large.")
    return context


def _fit_final_output_context(context: dict[str, Any]) -> dict[str, Any]:
    def encoded_size() -> int:
        return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))

    recent = context["recent_activity"]
    while encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS and len(recent) > 3:
        recent.pop(0)
    instructions = context["run_instructions"]
    while encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS and len(instructions) > 2:
        instructions.pop(0)
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        for entry in recent:
            entry["details"] = {}
        for field in (
            "important_facts",
            "open_issues",
            "actions_taken",
            "active_constraints",
        ):
            context["compact_memory"][field] = context["compact_memory"][field][-1:]
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        context["supervisor_base_instruction"] = _bounded_text(
            context["supervisor_base_instruction"], 240
        )
        context["recent_activity"] = recent[-1:]
        context["run_instructions"] = instructions[-1:]
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        context["order_id"] = _bounded_text(str(context["order_id"]), 128)
        context["order_state"] = _bounded_component(context["order_state"], max_chars=500)
        context["compact_memory"] = {
            "order_state": _bounded_text(context["compact_memory"]["order_state"], 120),
            "important_facts": [],
            "open_issues": [],
            "actions_taken": context["compact_memory"]["actions_taken"][-1:],
            "active_constraints": [],
            "next_review": _bounded_text(context["compact_memory"]["next_review"], 80),
        }
        context["recent_activity"] = []
    if encoded_size() > SUPERVISOR_CONTEXT_MAX_CHARS:
        raise SupervisorResponseInvalid("The bounded final-output context is too large.")
    return context


def _bounded_text(value: str, limit: int = MAX_CONTEXT_STRING_LENGTH) -> str:
    stripped = value.strip()
    return stripped if len(stripped) <= limit else f"{stripped[: limit - 1]}…"


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[bounded]"
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:12]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bounded_value(item, depth=depth + 1) for item in list(value)[:12]]
    return _bounded_text(str(value))


__all__ = [
    "DeterministicSupervisorProvider",
    "INSTRUCTION_CONTEXT_LIMIT",
    "OLLAMA_NUM_CTX",
    "OLLAMA_NUM_PREDICT",
    "OllamaSupervisorProvider",
    "RECENT_ACTIVITY_CONTEXT_LIMIT",
    "SUPERVISOR_CONTEXT_MAX_CHARS",
    "SupervisorProvider",
    "SupervisorProviderError",
    "SupervisorProviderUnavailable",
    "SupervisorResponseInvalid",
    "build_final_output_messages",
    "build_provider",
    "build_supervisor_context",
    "build_supervisor_messages",
    "normalize_provider_name",
    "ollama_json_schema",
]
