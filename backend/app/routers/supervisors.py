"""Supervisor configuration endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.supervisors import create_supervisor, get_supervisor, list_supervisors
from app.routers.dependencies import DatabaseSession
from app.schemas.supervisors import SupervisorCreate, SupervisorResponse

router = APIRouter(prefix="/api/supervisors", tags=["supervisors"])


def _database_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "database_unavailable",
            "message": "PostgreSQL is currently unavailable.",
        },
    )


@router.post("", response_model=SupervisorResponse, status_code=status.HTTP_201_CREATED)
async def create_supervisor_config(
    payload: SupervisorCreate,
    session: DatabaseSession,
) -> SupervisorResponse:
    """Create one reusable, secret-free supervisor configuration."""

    try:
        supervisor = await create_supervisor(
            session,
            name=payload.name,
            base_instruction=payload.base_instruction,
            available_actions=[action.value for action in payload.available_actions],
            default_wake_seconds=payload.default_wake_seconds,
            wake_aggressiveness=payload.wake_aggressiveness.value,
            model_configuration=payload.model_configuration.model_dump(exclude_none=True),
        )
        await session.commit()
        await session.refresh(supervisor)
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _database_unavailable(exc) from exc

    return SupervisorResponse.model_validate(supervisor)


@router.get("", response_model=list[SupervisorResponse])
async def get_supervisor_configs(session: DatabaseSession) -> list[SupervisorResponse]:
    """List supervisor configurations in creation order."""

    try:
        supervisors = await list_supervisors(session)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc
    return [SupervisorResponse.model_validate(item) for item in supervisors]


@router.get("/{supervisor_id}", response_model=SupervisorResponse)
async def get_supervisor_config(
    supervisor_id: UUID,
    session: DatabaseSession,
) -> SupervisorResponse:
    """Return one supervisor configuration or a controlled 404."""

    try:
        supervisor = await get_supervisor(session, supervisor_id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    if supervisor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "supervisor_not_found",
                "message": "Supervisor configuration was not found.",
            },
        )
    return SupervisorResponse.model_validate(supervisor)
