"""Supervisor and final-output Activities backed by the configured provider."""

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import get_settings
from app.supervisor.providers import (
    SupervisorProviderUnavailable,
    SupervisorResponseInvalid,
    build_provider,
)
from app.supervisor.schemas import decision_to_data, final_output_to_data
from app.temporal.models import (
    AgentDecisionData,
    FinalOutputActivityResult,
    FinalOutputRequest,
    SupervisorRequest,
)

# The Stage 1/2 Activity type is retained for caller and custom-Worker compatibility.
# Because its result schema evolved, deployments must drain pre-Stage-3 open histories.
FAKE_SUPERVISOR_ACTIVITY_NAME = "fake_supervisor"
FINAL_OUTPUT_ACTIVITY_NAME = "generate_final_output"


@activity.defn(name=FAKE_SUPERVISOR_ACTIVITY_NAME)
async def fake_supervisor(request: SupervisorRequest) -> AgentDecisionData:
    """Run one validated decision through deterministic rules or local Ollama."""

    provider = _build_activity_provider(request.provider, request.model)
    try:
        decision = await provider.decide(request)
    except SupervisorResponseInvalid:
        raise ApplicationError(
            "The supervisor returned a decision that failed validation.",
            type="InvalidSupervisorDecision",
        ) from None
    except SupervisorProviderUnavailable:
        raise ApplicationError(
            "The configured supervisor provider is unavailable.",
            type="SupervisorProviderUnavailable",
        ) from None

    return decision_to_data(
        decision,
        trigger=request.trigger,
        provider=provider.name,
        model=provider.model_name,
    )


@activity.defn(name=FINAL_OUTPUT_ACTIVITY_NAME)
async def generate_final_output(request: FinalOutputRequest) -> FinalOutputActivityResult:
    """Generate a validated report after Workflow-owned lifecycle authorization."""

    provider = _build_activity_provider(request.provider, request.model)
    try:
        output = await provider.finalize(request)
    except SupervisorResponseInvalid:
        raise ApplicationError(
            "The supervisor returned final output that failed validation.",
            type="InvalidFinalOutput",
        ) from None
    except SupervisorProviderUnavailable:
        raise ApplicationError(
            "The configured supervisor provider is unavailable during finalization.",
            type="SupervisorProviderUnavailable",
        ) from None

    return FinalOutputActivityResult(
        output=final_output_to_data(output),
        provider=provider.name,
        model=provider.model_name,
    )


def _build_activity_provider(provider_name: str, model: str | None):
    try:
        return build_provider(
            provider_name,
            model=model,
            settings=get_settings(),
        )
    except ValueError:
        raise ApplicationError(
            "The supervisor provider configuration is invalid.",
            type="InvalidSupervisorProvider",
            non_retryable=True,
        ) from None


__all__ = [
    "FAKE_SUPERVISOR_ACTIVITY_NAME",
    "FINAL_OUTPUT_ACTIVITY_NAME",
    "fake_supervisor",
    "generate_final_output",
]
