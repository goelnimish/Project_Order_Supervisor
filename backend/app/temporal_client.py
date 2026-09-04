"""Reusable Temporal client connection factory."""

from temporalio.client import Client

from app.config import Settings, get_settings


async def connect_temporal(configuration: Settings | None = None) -> Client:
    """Connect on demand so API startup does not depend on Temporal Server."""

    settings = configuration or get_settings()
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
