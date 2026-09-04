"""Read global and per-run Stage 3.5 analytics through the public FastAPI API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any
from uuid import UUID

import httpx

API_TIMEOUT_SECONDS = 10.0
ACTION_DISTRIBUTION_KEYS = {
    "message_fulfillment_team",
    "message_payments_team",
    "message_logistics_team",
    "message_customer",
    "create_internal_note",
}


class DemoError(RuntimeError):
    """A concise, user-actionable analytics demo failure."""


def parse_args() -> argparse.Namespace:
    """Parse the intentionally small read-only demo surface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="FastAPI base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--run-id",
        type=UUID,
        required=True,
        help="Persisted run UUID to inspect",
    )
    return parser.parse_args()


async def get_json(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """Read one analytics resource without exposing raw response bodies."""

    try:
        response = await client.get(path)
    except (httpx.TimeoutException, httpx.RequestError) as error:
        raise DemoError("FastAPI is unavailable. Start the API and retry the demo.") from error
    if response.status_code != 200:
        raise DemoError(f"GET {path} returned HTTP {response.status_code}; expected HTTP 200.")
    try:
        payload = response.json()
    except ValueError as error:
        raise DemoError(f"GET {path} returned unreadable JSON.") from error
    if not isinstance(payload, dict):
        raise DemoError(f"GET {path} returned an unexpected response shape.")
    return payload


def verify_action_distribution(summary: dict[str, Any]) -> None:
    """Require the stable five-action analytics contract, including zero counts."""

    distribution = summary.get("action_distribution")
    if not isinstance(distribution, dict) or set(distribution) != ACTION_DISTRIBUTION_KEYS:
        raise DemoError("Global analytics did not return exactly the five required action names.")


async def run_demo(api_url: str, run_id: UUID) -> None:
    """Read and print both Stage 3.5 analytics resources without mutating state."""

    async with httpx.AsyncClient(
        base_url=api_url.rstrip("/"),
        timeout=API_TIMEOUT_SECONDS,
    ) as client:
        summary = await get_json(client, "/api/analytics/summary")
        run_analytics = await get_json(client, f"/api/runs/{run_id}/analytics")

    verify_action_distribution(summary)
    if run_analytics.get("run_id") != str(run_id):
        raise DemoError("Per-run analytics returned a different run ID.")

    print("\n=== Global analytics ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("\n=== Per-run analytics ===")
    print(json.dumps(run_analytics, indent=2, sort_keys=True))
    print("\nSTAGE 3.5 ANALYTICS DEMO PASSED")


def main() -> int:
    """Run the async demo and return a shell-friendly status."""

    args = parse_args()
    try:
        asyncio.run(run_demo(args.api_url, args.run_id))
    except DemoError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError):
        print("ERROR: FastAPI returned an unexpected analytics response shape.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
