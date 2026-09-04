"""Focused Stage 2 API tests with PostgreSQL and a recording Temporal fake."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.database import get_session
from app.main import app
from app.repositories.runs import mark_run_completed, update_run_state
from app.routers.dependencies import get_optional_temporal_client, get_temporal_client
from app.temporal.models import (
    BUSINESS_ACTION_NAMES,
    InterruptRequest,
    OrderEvent,
    PauseRequest,
    ResumeRequest,
    RunInstruction,
    TerminateRequest,
    WorkflowInput,
)

ASSIGNMENT_EVENT_TYPES = (
    "order_created",
    "payment_confirmed",
    "payment_failed",
    "shipment_created",
    "shipment_delayed",
    "delivered",
    "refund_requested",
    "customer_message_received",
    "no_update_for_n_hours",
)


@dataclass(frozen=True)
class StartDispatch:
    """One Workflow start captured by the Temporal test double."""

    workflow_id: str
    task_queue: str
    workflow_input: WorkflowInput


@dataclass(frozen=True)
class SignalDispatch:
    """One Workflow Signal captured by the Temporal test double."""

    workflow_id: str
    temporal_run_id: str | None
    signal_name: str
    payload: object


class RecordingWorkflowHandle:
    """Small handle implementing the methods exercised by the API routers."""

    def __init__(
        self,
        client: RecordingTemporalClient,
        workflow_id: str,
        temporal_run_id: str | None,
    ) -> None:
        self._client = client
        self._workflow_id = workflow_id
        self._temporal_run_id = temporal_run_id

    async def signal(self, signal: object, payload: object) -> None:
        self._client.signals.append(
            SignalDispatch(
                workflow_id=self._workflow_id,
                temporal_run_id=self._temporal_run_id,
                signal_name=getattr(signal, "__name__", str(signal)),
                payload=payload,
            )
        )

    async def query(self, _query: object) -> None:
        raise RuntimeError("The API test double does not emulate live Workflow Query state.")


class RecordingStartedHandle:
    """Start response carrying the Temporal run identity persisted by the API."""

    def __init__(self, first_execution_run_id: str) -> None:
        self.first_execution_run_id = first_execution_run_id


class RecordingTemporalClient:
    """Record Workflow starts and Signals without contacting Temporal Server."""

    def __init__(self) -> None:
        self.starts: list[StartDispatch] = []
        self.signals: list[SignalDispatch] = []

    async def start_workflow(
        self,
        _workflow: object,
        workflow_input: WorkflowInput,
        *,
        id: str,
        task_queue: str,
    ) -> RecordingStartedHandle:
        self.starts.append(
            StartDispatch(
                workflow_id=id,
                task_queue=task_queue,
                workflow_input=workflow_input,
            )
        )
        return RecordingStartedHandle(first_execution_run_id=str(uuid4()))

    def get_workflow_handle(
        self,
        workflow_id: str,
        *,
        first_execution_run_id: str | None = None,
    ) -> RecordingWorkflowHandle:
        return RecordingWorkflowHandle(self, workflow_id, first_execution_run_id)


@dataclass
class ApiHarness:
    """HTTP client plus the isolated database session and Temporal recorder."""

    client: AsyncClient
    session: AsyncSession
    temporal: RecordingTemporalClient


api_test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest_asyncio.fixture
async def api_harness() -> AsyncIterator[ApiHarness]:
    """Contain route commits inside a rollback-only outer PostgreSQL transaction."""

    async with api_test_engine.connect() as connection:
        outer_transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        temporal = RecordingTemporalClient()

        async def override_session() -> AsyncIterator[AsyncSession]:
            yield session

        async def override_temporal_client() -> RecordingTemporalClient:
            return temporal

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_temporal_client] = override_temporal_client
        app.dependency_overrides[get_optional_temporal_client] = override_temporal_client
        transport = ASGITransport(app=app)

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield ApiHarness(client=client, session=session, temporal=temporal)
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_temporal_client, None)
            app.dependency_overrides.pop(get_optional_temporal_client, None)
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()


def _supervisor_payload() -> dict[str, Any]:
    token = uuid4().hex
    return {
        "name": f"API supervisor {token}",
        "base_instruction": "Keep this isolated API test order moving safely.",
        "available_actions": [
            "message_fulfillment_team",
            "message_payments_team",
            "message_logistics_team",
            "message_customer",
            "create_internal_note",
        ],
        "default_wake_seconds": 90,
        "wake_aggressiveness": "moderate",
        "model_config": {"provider": "none"},
    }


async def _create_supervisor(harness: ApiHarness) -> dict[str, Any]:
    response = await harness.client.post("/api/supervisors", json=_supervisor_payload())
    assert response.status_code == 201, response.text
    return response.json()


async def _start_run(
    harness: ApiHarness,
    supervisor_id: str,
    *,
    order_id: str | None = None,
) -> dict[str, Any]:
    selected_order_id = order_id or f"api-order-{uuid4().hex}"
    response = await harness.client.post(
        "/api/runs",
        json={
            "order_id": selected_order_id,
            "supervisor_config_id": supervisor_id,
            "order_context": {
                "customer_id": f"customer-{uuid4().hex}",
                "initial_state": {"priority": "standard"},
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _set_run_status(harness: ApiHarness, run_id: str, status: str) -> None:
    updated = await update_run_state(harness.session, UUID(run_id), status=status)
    assert updated is not None
    await harness.session.commit()


@pytest.mark.asyncio
async def test_supervisor_create_get_list_and_validation(api_harness: ApiHarness) -> None:
    supervisor = await _create_supervisor(api_harness)

    get_response = await api_harness.client.get(f"/api/supervisors/{supervisor['id']}")
    list_response = await api_harness.client.get("/api/supervisors")

    assert get_response.status_code == 200
    assert get_response.json() == supervisor
    assert list_response.status_code == 200
    assert supervisor["id"] in {item["id"] for item in list_response.json()}
    assert supervisor["name"].startswith("API supervisor ")
    assert supervisor["base_instruction"] == ("Keep this isolated API test order moving safely.")
    assert supervisor["available_actions"] == list(BUSINESS_ACTION_NAMES)
    assert supervisor["default_wake_seconds"] == 90
    assert supervisor["wake_aggressiveness"] == "moderate"
    assert supervisor["model_config"] == {"provider": "none", "model": None, "temperature": None}

    unknown_action_payload = _supervisor_payload()
    unknown_action_payload["available_actions"] = ["send_sms"]
    unknown_action = await api_harness.client.post("/api/supervisors", json=unknown_action_payload)

    secret_field_payload = _supervisor_payload()
    secret_field_payload["model_config"] = {"api_key": "AUDIT_SECRET_SENTINEL"}
    secret_field = await api_harness.client.post("/api/supervisors", json=secret_field_payload)

    assert unknown_action.status_code == 422
    assert secret_field.status_code == 422
    assert "AUDIT_SECRET_SENTINEL" not in secret_field.text
    assert all("input" not in error for error in secret_field.json()["detail"])


@pytest.mark.asyncio
async def test_run_detail_keeps_postgres_available_without_temporal(
    api_harness: ApiHarness,
) -> None:
    supervisor = await _create_supervisor(api_harness)
    run = await _start_run(api_harness, supervisor["id"])

    async def unavailable_optional_temporal_client() -> None:
        return None

    app.dependency_overrides[get_optional_temporal_client] = unavailable_optional_temporal_client
    response = await api_harness.client.get(f"/api/runs/{run['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == run["id"]
    assert response.json()["workflow_state"] is None


@pytest.mark.asyncio
async def test_run_create_list_get_and_conflicts(api_harness: ApiHarness) -> None:
    supervisor = await _create_supervisor(api_harness)
    order_id = f"api-order-{uuid4().hex}"

    unknown_supervisor = await api_harness.client.post(
        "/api/runs",
        json={
            "order_id": f"unknown-supervisor-order-{uuid4().hex}",
            "supervisor_config_id": str(uuid4()),
            "order_context": {"initial_state": {}},
        },
    )
    run = await _start_run(api_harness, supervisor["id"], order_id=order_id)
    duplicate = await api_harness.client.post(
        "/api/runs",
        json={
            "order_id": order_id,
            "supervisor_config_id": supervisor["id"],
            "order_context": {"initial_state": {}},
        },
    )
    list_response = await api_harness.client.get("/api/runs")
    filtered_response = await api_harness.client.get("/api/runs", params={"status": "starting"})
    detail_response = await api_harness.client.get(f"/api/runs/{run['id']}")

    assert unknown_supervisor.status_code == 404
    assert unknown_supervisor.json()["detail"]["code"] == "supervisor_not_found"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "active_order_run_exists"
    assert len(api_harness.temporal.starts) == 1
    assert api_harness.temporal.starts[0].workflow_id == f"order-supervisor:{order_id}"
    assert api_harness.temporal.starts[0].workflow_input.run_id == run["id"]
    assert run["temporal_run_id"] is not None

    assert list_response.status_code == 200
    assert run["id"] in {item["id"] for item in list_response.json()}
    assert filtered_response.status_code == 200
    assert run["id"] in {item["id"] for item in filtered_response.json()}
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == run["id"]
    assert detail_response.json()["activities"] == []
    assert detail_response.json()["workflow_state"] is None


@pytest.mark.asyncio
async def test_ollama_runtime_selection_is_frozen_into_workflow_input(
    api_harness: ApiHarness,
) -> None:
    payload = _supervisor_payload()
    payload["model_config"] = {
        "provider": "ollama",
        "model": "qwen3:1.7b",
        "temperature": 0,
    }
    supervisor_response = await api_harness.client.post("/api/supervisors", json=payload)
    assert supervisor_response.status_code == 201

    await _start_run(api_harness, supervisor_response.json()["id"])

    workflow_input = api_harness.temporal.starts[-1].workflow_input
    assert workflow_input.supervisor_provider == "ollama"
    assert workflow_input.supervisor_model == "qwen3:1.7b"
    assert workflow_input.wake_aggressiveness == "moderate"


@pytest.mark.asyncio
async def test_event_instruction_and_control_signals_dispatch_correctly(
    api_harness: ApiHarness,
) -> None:
    supervisor = await _create_supervisor(api_harness)
    run = await _start_run(api_harness, supervisor["id"])
    run_path = f"/api/runs/{run['id']}"

    event_response = await api_harness.client.post(
        f"{run_path}/events",
        json={
            "event_id": f"api-event-{uuid4().hex}",
            "event_type": "shipment_delayed",
            "occurred_at": "2026-09-04T12:00:00Z",
            "payload": {"delay_minutes": 30},
        },
    )
    instruction_response = await api_harness.client.post(
        f"{run_path}/instructions",
        json={"instruction": "For this order, prioritize speed over cost."},
    )
    pause_response = await api_harness.client.post(
        f"{run_path}/pause", json={"reason": "Review the carrier update."}
    )

    await _set_run_status(api_harness, run["id"], "paused")
    resume_response = await api_harness.client.post(f"{run_path}/resume")

    await _set_run_status(api_harness, run["id"], "running")
    interrupt_response = await api_harness.client.post(
        f"{run_path}/interrupt", json={"reason": "Human review is required."}
    )

    await _set_run_status(api_harness, run["id"], "interrupted")
    resume_from_interrupt = await api_harness.client.post(f"{run_path}/resume")

    await _set_run_status(api_harness, run["id"], "running")
    terminate_response = await api_harness.client.post(
        f"{run_path}/terminate", json={"reason": "End the API test run."}
    )

    responses = (
        event_response,
        instruction_response,
        pause_response,
        resume_response,
        interrupt_response,
        resume_from_interrupt,
        terminate_response,
    )
    assert all(response.status_code == 202 for response in responses)
    assert [dispatch.signal_name for dispatch in api_harness.temporal.signals] == [
        "receive_event",
        "add_instruction",
        "pause",
        "resume",
        "interrupt",
        "resume",
        "request_termination",
    ]
    assert all(
        dispatch.workflow_id == run["workflow_id"] for dispatch in api_harness.temporal.signals
    )
    assert all(
        dispatch.temporal_run_id == run["temporal_run_id"]
        for dispatch in api_harness.temporal.signals
    )

    event, instruction, pause, resume, interrupt, second_resume, terminate = (
        dispatch.payload for dispatch in api_harness.temporal.signals
    )
    assert isinstance(event, OrderEvent)
    assert event.event_type.value == "shipment_delayed"
    assert event.payload == {"delay_minutes": 30}
    assert isinstance(instruction, RunInstruction)
    assert instruction.instruction == "For this order, prioritize speed over cost."
    assert isinstance(pause, PauseRequest)
    assert pause.reason == "Review the carrier update."
    assert isinstance(resume, ResumeRequest)
    assert isinstance(interrupt, InterruptRequest)
    assert interrupt.reason == "Human review is required."
    assert isinstance(second_resume, ResumeRequest)
    assert isinstance(terminate, TerminateRequest)
    assert terminate.reason == "End the API test run."


@pytest.mark.asyncio
async def test_invalid_transition_and_terminal_event_are_rejected(
    api_harness: ApiHarness,
) -> None:
    supervisor = await _create_supervisor(api_harness)
    run = await _start_run(api_harness, supervisor["id"])
    run_path = f"/api/runs/{run['id']}"

    invalid_resume = await api_harness.client.post(f"{run_path}/resume")
    assert invalid_resume.status_code == 409
    assert invalid_resume.json()["detail"]["code"] == "invalid_lifecycle_transition"
    assert api_harness.temporal.signals == []

    completed = await mark_run_completed(
        api_harness.session,
        UUID(run["id"]),
        status="completed",
        completion_reason="Terminal API test state.",
    )
    assert completed is not None
    await api_harness.session.commit()

    terminal_event = await api_harness.client.post(
        f"{run_path}/events",
        json={
            "event_id": f"terminal-event-{uuid4().hex}",
            "event_type": "payment_confirmed",
            "occurred_at": "2026-09-04T12:00:00Z",
            "payload": {},
        },
    )

    assert terminal_event.status_code == 409
    assert terminal_event.json()["detail"]["code"] == "invalid_lifecycle_transition"
    assert api_harness.temporal.signals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ASSIGNMENT_EVENT_TYPES)
async def test_assignment_event_types_are_accepted_by_event_api(
    api_harness: ApiHarness,
    event_type: str,
) -> None:
    """AT-28: every event named by the assignment reaches the Workflow Signal contract."""

    supervisor = await _create_supervisor(api_harness)
    run = await _start_run(api_harness, supervisor["id"])
    event_id = f"assignment-{event_type}-{uuid4().hex}"

    response = await api_harness.client.post(
        f"/api/runs/{run['id']}/events",
        json={
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": "2026-09-04T12:00:00Z",
            "payload": {"source": "assignment-acceptance"},
        },
    )

    assert response.status_code == 202, response.text
    dispatched = api_harness.temporal.signals[-1]
    assert dispatched.signal_name == "receive_event"
    assert isinstance(dispatched.payload, OrderEvent)
    assert dispatched.payload.event_id == event_id
    assert dispatched.payload.event_type.value == event_type


@pytest.mark.asyncio
async def test_unknown_resources_and_malformed_json_return_controlled_responses(
    api_harness: ApiHarness,
) -> None:
    """AT-72, AT-73, AT-76: core resource failures remain explicit and sanitized."""

    unknown_id = str(uuid4())
    unknown_supervisor = await api_harness.client.get(f"/api/supervisors/{unknown_id}")
    unknown_run = await api_harness.client.get(f"/api/runs/{unknown_id}")
    malformed_json = await api_harness.client.post(
        "/api/supervisors",
        content=b'{"name":',
        headers={"content-type": "application/json"},
    )

    assert unknown_supervisor.status_code == 404
    assert unknown_supervisor.json()["detail"]["code"] == "supervisor_not_found"
    assert unknown_run.status_code == 404
    assert unknown_run.json()["detail"]["code"] == "run_not_found"
    assert malformed_json.status_code == 422
    assert malformed_json.json()["detail"]
    assert all("input" not in error for error in malformed_json.json()["detail"])
