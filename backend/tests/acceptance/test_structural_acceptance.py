"""Small structural assertions for project boundaries best checked in source."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.main import app
from app.routers.runs import terminate_run

BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_SOURCE = BACKEND_ROOT / "app" / "temporal" / "workflows.py"


def _qualified_call_name(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.expr = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_workflow_uses_temporal_durable_waits_without_busy_polling() -> None:
    """AT-12: waiting is Temporal-owned and contains no application sleep loop."""

    tree = ast.parse(WORKFLOW_SOURCE.read_text(encoding="utf-8"))
    calls = [_qualified_call_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert calls.count("workflow.wait_condition") >= 2
    assert "workflow.sleep" in calls  # bounded persistence-recovery timer
    assert "asyncio.sleep" not in calls
    assert "time.sleep" not in calls


def test_normal_termination_is_signal_driven_and_no_ai_close_route_exists() -> None:
    """AT-39/AT-42: normal product flow exposes graceful Signal termination only."""

    route_paths = set(app.openapi()["paths"])
    terminate_source = inspect.getsource(terminate_run)

    assert "/api/runs/{run_id}/terminate" in route_paths
    assert not any("close_workflow" in path for path in route_paths)
    assert "request_termination" in terminate_source
    assert "_send_signal" in terminate_source
    assert ".terminate(" not in terminate_source
