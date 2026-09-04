"""Run-management, event, instruction, and lifecycle endpoints."""

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.models.run import Run
from app.repositories.activities import list_activities_for_run
from app.repositories.runs import (
    create_run,
    get_active_run_for_order,
    get_run,
    list_runs,
    mark_run_completed,
    update_run_state,
)
from app.repositories.supervisors import get_supervisor
from app.routers.dependencies import DatabaseSession, OptionalTemporalClient, TemporalClient
from app.schemas.controls import (
    InstructionRequest,
    InterruptRunRequest,
    PauseRunRequest,
    SignalAcceptedResponse,
    TerminateRunRequest,
)
from app.schemas.events import OrderEventRequest
from app.schemas.runs import (
    ActivityResponse,
    RunCreate,
    RunDetailResponse,
    RunResponse,
    WorkflowStateResponse,
)
from app.supervisor.providers import normalize_provider_name
from app.temporal.models import (
    EventType,
    InterruptRequest,
    OrderContext,
    OrderEvent,
    PauseRequest,
    ResumeRequest,
    RunInstruction,
    TerminateRequest,
    WorkflowInput,
    WorkflowStatus,
    workflow_id_for_order,
)
from app.temporal.workflows import OrderSupervisorWorkflow

router = APIRouter(prefix="/api/runs", tags=["runs"])

TERMINAL_STATUSES = {
    WorkflowStatus.COMPLETED.value,
    WorkflowStatus.TERMINATED.value,
    WorkflowStatus.FAILED.value,
}
AUTOMATED_STATUSES = {
    WorkflowStatus.STARTING.value,
    WorkflowStatus.RUNNING.value,
    WorkflowStatus.SLEEPING.value,
}
BLOCKED_STATUSES = {
    WorkflowStatus.PAUSED.value,
    WorkflowStatus.INTERRUPTED.value,
}


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _database_unavailable(exc: Exception) -> HTTPException:
    return _api_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "database_unavailable",
        "PostgreSQL is currently unavailable.",
    )


def _temporal_unavailable(exc: Exception) -> HTTPException:
    return _api_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "temporal_unavailable",
        "Temporal did not accept the request.",
    )


async def _load_run_or_404(session: DatabaseSession, run_id: UUID) -> Run:
    try:
        run = await get_run(session, run_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc
    if run is None:
        raise _api_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            "Supervisor run was not found.",
        )
    return run


def _ensure_transition(run: Run, allowed: set[str], transition: str) -> None:
    if run.status not in allowed or run.completed_at is not None:
        raise _api_error(
            status.HTTP_409_CONFLICT,
            "invalid_lifecycle_transition",
            f"Cannot {transition} a run while its status is {run.status}.",
        )


def _workflow_handle(client: TemporalClient, run: Run):
    return client.get_workflow_handle(
        run.workflow_id,
        first_execution_run_id=run.temporal_run_id,
    )


def _resolve_supervisor_runtime(supervisor) -> tuple[str, str | None]:
    """Freeze secret-free provider selection into deterministic Workflow input."""

    settings = get_settings()
    configured_provider = supervisor.model_configuration.get("provider")
    default_provider = settings.llm_provider
    try:
        provider = normalize_provider_name(configured_provider, default=default_provider)
    except ValueError as exc:
        raise _api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_supervisor_provider",
            "Supervisor provider must be deterministic or ollama.",
        ) from exc
    configured_model = supervisor.model_configuration.get("model")
    model = (configured_model or settings.ollama_model) if provider == "ollama" else None
    return provider, model


async def _send_signal(
    *,
    client: TemporalClient,
    run: Run,
    signal,
    payload,
    signal_name: str,
) -> SignalAcceptedResponse:
    try:
        await _workflow_handle(client, run).signal(signal, payload)
    except Exception as exc:
        raise _temporal_unavailable(exc) from exc
    return SignalAcceptedResponse(
        run_id=run.id,
        workflow_id=run.workflow_id,
        signal=signal_name,
    )


async def _mark_start_failed(
    session: DatabaseSession,
    run: Run,
    reason: str,
) -> None:
    """Best-effort explicit compensation for the non-transactional start boundary."""

    try:
        await mark_run_completed(
            session,
            run.id,
            status=WorkflowStatus.FAILED.value,
            completion_reason=reason,
            completed_at=datetime.now(UTC),
        )
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()


@router.post("", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def start_run(
    payload: RunCreate,
    session: DatabaseSession,
    temporal_client: TemporalClient,
) -> RunResponse:
    """Persist a starting run, then start its deterministic Temporal Workflow."""

    try:
        supervisor = await get_supervisor(session, payload.supervisor_config_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc
    if supervisor is None:
        raise _api_error(
            status.HTTP_404_NOT_FOUND,
            "supervisor_not_found",
            "Supervisor configuration was not found.",
        )

    supervisor_provider, supervisor_model = _resolve_supervisor_runtime(supervisor)

    try:
        active = await get_active_run_for_order(session, payload.order_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc
    if active is not None:
        raise _api_error(
            status.HTTP_409_CONFLICT,
            "active_order_run_exists",
            "An active supervisor already exists for this order.",
        )

    run_id = uuid4()
    workflow_id = workflow_id_for_order(payload.order_id)
    order_context = {
        "order_id": payload.order_id,
        "customer_id": payload.order_context.customer_id,
        "initial_state": payload.order_context.initial_state,
    }
    initial_order_state = {
        "lifecycle": "open",
        "payment": "pending",
        "shipment": "not_created",
        "refund": "not_requested",
        "last_event_type": None,
        "event_count": 0,
        "attributes": dict(payload.order_context.initial_state),
    }

    try:
        run = await create_run(
            session,
            run_id=run_id,
            order_id=payload.order_id,
            workflow_id=workflow_id,
            supervisor_config_id=supervisor.id,
            status=WorkflowStatus.STARTING.value,
            current_order_state=initial_order_state,
            order_context=order_context,
            additional_instructions=[],
            memory_summary=None,
            final_output=None,
            next_wake_at=None,
            started_at=datetime.now(UTC),
        )
        await session.commit()
        await session.refresh(run)
    except IntegrityError as exc:
        await session.rollback()
        raise _api_error(
            status.HTTP_409_CONFLICT,
            "active_order_run_exists",
            "An active supervisor already exists for this order.",
        ) from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _database_unavailable(exc) from exc

    try:
        handle = await temporal_client.start_workflow(
            OrderSupervisorWorkflow.run,
            WorkflowInput(
                order=OrderContext(
                    order_id=payload.order_id,
                    customer_id=payload.order_context.customer_id,
                    initial_state=dict(payload.order_context.initial_state),
                ),
                supervisor_instructions=supervisor.base_instruction,
                demo_wake_interval_seconds=supervisor.default_wake_seconds,
                run_id=str(run.id),
                available_actions=list(supervisor.available_actions),
                supervisor_provider=supervisor_provider,
                supervisor_model=supervisor_model,
                wake_aggressiveness=supervisor.wake_aggressiveness,
            ),
            id=workflow_id,
            task_queue=get_settings().temporal_task_queue,
        )
    except WorkflowAlreadyStartedError as exc:
        await _mark_start_failed(
            session,
            run,
            "Temporal reported that this order Workflow already exists.",
        )
        raise _api_error(
            status.HTTP_409_CONFLICT,
            "workflow_already_exists",
            "A Temporal Workflow already exists for this order.",
        ) from exc
    except Exception as exc:
        await _mark_start_failed(
            session,
            run,
            "Temporal start failed; inspect the deterministic Workflow ID before retrying.",
        )
        raise _temporal_unavailable(exc) from exc

    try:
        await update_run_state(
            session,
            run.id,
            temporal_run_id=handle.first_execution_run_id,
        )
        await session.commit()
        await session.refresh(run)
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _database_unavailable(exc) from exc

    return RunResponse.model_validate(run)


@router.get("", response_model=list[RunResponse])
async def get_runs(
    session: DatabaseSession,
    run_status: WorkflowStatus | None = Query(default=None, alias="status"),
) -> list[RunResponse]:
    """List persisted runs, optionally filtered by lifecycle status."""

    try:
        runs = await list_runs(session, status=run_status.value if run_status else None)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc
    return [RunResponse.model_validate(run) for run in runs]


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run_detail(
    run_id: UUID,
    session: DatabaseSession,
    temporal_client: OptionalTemporalClient,
) -> RunDetailResponse:
    """Return the PostgreSQL audit view plus a best-effort live Workflow Query."""

    run = await _load_run_or_404(session, run_id)
    try:
        activities = await list_activities_for_run(session, run.id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    workflow_state: WorkflowStateResponse | None = None
    if temporal_client is not None:
        try:
            snapshot = await _workflow_handle(temporal_client, run).query(
                OrderSupervisorWorkflow.get_state
            )
            workflow_state = WorkflowStateResponse.model_validate(asdict(snapshot))
        except Exception:
            # PostgreSQL remains useful if a live/closed Workflow cannot currently be queried.
            workflow_state = None

    run_data = RunResponse.model_validate(run).model_dump()
    return RunDetailResponse(
        **run_data,
        activities=[ActivityResponse.model_validate(item) for item in activities],
        workflow_state=workflow_state,
    )


@router.post(
    "/{run_id}/events",
    response_model=SignalAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def inject_event(
    run_id: UUID,
    payload: OrderEventRequest,
    session: DatabaseSession,
    temporal_client: TemporalClient,
) -> SignalAcceptedResponse:
    """Signal an order event; the Workflow remains responsible for processing it."""

    run = await _load_run_or_404(session, run_id)
    _ensure_transition(run, AUTOMATED_STATUSES | BLOCKED_STATUSES, "accept an event for")
    event = OrderEvent(
        event_id=payload.event_id,
        event_type=EventType(payload.event_type),
        occurred_at=payload.occurred_at.isoformat(),
        payload=dict(payload.payload),
    )
    return await _send_signal(
        client=temporal_client,
        run=run,
        signal=OrderSupervisorWorkflow.receive_event,
        payload=event,
        signal_name="receive_event",
    )


@router.post(
    "/{run_id}/instructions",
    response_model=SignalAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_instruction(
    run_id: UUID,
    payload: InstructionRequest,
    session: DatabaseSession,
    temporal_client: TemporalClient,
) -> SignalAcceptedResponse:
    """Add durable run-specific guidance to a live Workflow."""

    run = await _load_run_or_404(session, run_id)
    _ensure_transition(run, AUTOMATED_STATUSES | BLOCKED_STATUSES, "add an instruction to")
    instruction = RunInstruction(
        instruction_id=str(uuid4()),
        instruction=payload.instruction,
        created_at=datetime.now(UTC).isoformat(),
    )
    return await _send_signal(
        client=temporal_client,
        run=run,
        signal=OrderSupervisorWorkflow.add_instruction,
        payload=instruction,
        signal_name="add_instruction",
    )


@router.post(
    "/{run_id}/pause",
    response_model=SignalAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def pause_run(
    run_id: UUID,
    session: DatabaseSession,
    temporal_client: TemporalClient,
    payload: PauseRunRequest | None = None,
) -> SignalAcceptedResponse:
    """Pause automated inference while continuing to accept queued events."""

    run = await _load_run_or_404(session, run_id)
    _ensure_transition(run, AUTOMATED_STATUSES, "pause")
    return await _send_signal(
        client=temporal_client,
        run=run,
        signal=OrderSupervisorWorkflow.pause,
        payload=PauseRequest(reason=payload.reason if payload else None),
        signal_name="pause",
    )


@router.post(
    "/{run_id}/resume",
    response_model=SignalAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_run(
    run_id: UUID,
    session: DatabaseSession,
    temporal_client: TemporalClient,
) -> SignalAcceptedResponse:
    """Resume a paused or interrupted Workflow."""

    run = await _load_run_or_404(session, run_id)
    _ensure_transition(run, BLOCKED_STATUSES, "resume")
    return await _send_signal(
        client=temporal_client,
        run=run,
        signal=OrderSupervisorWorkflow.resume,
        payload=ResumeRequest(),
        signal_name="resume",
    )


@router.post(
    "/{run_id}/interrupt",
    response_model=SignalAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def interrupt_run(
    run_id: UUID,
    payload: InterruptRunRequest,
    session: DatabaseSession,
    temporal_client: TemporalClient,
) -> SignalAcceptedResponse:
    """Enter the explicit human-review state until resume or terminate."""

    run = await _load_run_or_404(session, run_id)
    _ensure_transition(run, AUTOMATED_STATUSES | {WorkflowStatus.PAUSED.value}, "interrupt")
    return await _send_signal(
        client=temporal_client,
        run=run,
        signal=OrderSupervisorWorkflow.interrupt,
        payload=InterruptRequest(reason=payload.reason),
        signal_name="interrupt",
    )


@router.post(
    "/{run_id}/terminate",
    response_model=SignalAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def terminate_run(
    run_id: UUID,
    payload: TerminateRunRequest,
    session: DatabaseSession,
    temporal_client: TemporalClient,
) -> SignalAcceptedResponse:
    """Request graceful Workflow-owned termination; no hard terminate is exposed."""

    run = await _load_run_or_404(session, run_id)
    _ensure_transition(run, AUTOMATED_STATUSES | BLOCKED_STATUSES, "terminate")
    return await _send_signal(
        client=temporal_client,
        run=run,
        signal=OrderSupervisorWorkflow.request_termination,
        payload=TerminateRequest(reason=payload.reason),
        signal_name="request_termination",
    )
