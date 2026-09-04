"""Typed health-check response contracts."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """API process health response."""

    status: Literal["healthy"]
    service: Literal["order-supervisor-api"]


class DatabaseHealthResponse(BaseModel):
    """Credential-free PostgreSQL health response."""

    status: Literal["healthy", "unhealthy"]
    service: Literal["postgresql"]
    reachable: bool
