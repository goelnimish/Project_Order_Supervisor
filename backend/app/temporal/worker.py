"""Standalone Temporal Worker entry point."""

import asyncio
import logging

from temporalio.worker import Worker

from app.config import get_settings
from app.temporal.activities import fake_supervisor, generate_final_output
from app.temporal.business_actions import execute_business_action
from app.temporal.persistence_activities import persist_workflow_transition
from app.temporal.workflows import OrderSupervisorWorkflow
from app.temporal_client import connect_temporal

LOGGER = logging.getLogger(__name__)


async def run_worker() -> None:
    """Connect and poll until the process receives a shutdown signal."""

    settings = get_settings()
    client = await connect_temporal(settings)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[OrderSupervisorWorkflow],
        activities=[
            fake_supervisor,
            generate_final_output,
            execute_business_action,
            persist_workflow_transition,
        ],
    )
    LOGGER.info(
        "Temporal Worker starting: address=%s namespace=%s task_queue=%s",
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_task_queue,
    )
    await worker.run()


def main() -> None:
    """Configure console logging and run the worker."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        LOGGER.info("Temporal Worker stopped.")


if __name__ == "__main__":
    main()
