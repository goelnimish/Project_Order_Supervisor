"""Tests for controlled database health responses."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import database_is_reachable
from app.main import app


async def database_available() -> bool:
    return True


async def database_unavailable() -> bool:
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dependency", "expected_status", "expected_body"),
    [
        (
            database_available,
            200,
            {"status": "healthy", "service": "postgresql", "reachable": True},
        ),
        (
            database_unavailable,
            503,
            {"status": "unhealthy", "service": "postgresql", "reachable": False},
        ),
    ],
)
async def test_database_health_response(
    dependency,
    expected_status: int,
    expected_body: dict[str, str | bool],
) -> None:
    app.dependency_overrides[database_is_reachable] = dependency
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/database")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    assert response.json() == expected_body
