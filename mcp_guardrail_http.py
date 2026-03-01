#!/usr/bin/env python3
"""
MCP server that wraps the Agent Guardrail hosted API.

Usage:
    python mcp_guardrail_http.py --transport streamable-http
    python mcp_guardrail_http.py --transport stdio

Environment variables:
    GUARDRAIL_ENDPOINT  — Base URL of the guardrail API (e.g. https://guardrail.example.com)
    GUARDRAIL_API_KEY   — Admin API key for authentication
"""

import argparse
import json
import os
import urllib.request
import urllib.error
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GUARDRAIL_ENDPOINT = os.environ.get("GUARDRAIL_ENDPOINT", "")
GUARDRAIL_API_KEY = os.environ.get("GUARDRAIL_API_KEY", "")
DEFAULT_PORT = int(os.environ.get("PORT", "8200"))

mcp = FastMCP("Agent Guardrail")

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _api_call(method: str, path: str, data: dict | None = None) -> dict[str, Any]:
    """Make an HTTP request to the Guardrail API and return parsed JSON."""
    url = f"{GUARDRAIL_ENDPOINT.rstrip('/')}{path}"

    headers = {
        "Content-Type": "application/json",
        "X-Admin-Key": GUARDRAIL_API_KEY,
    }

    body = json.dumps(data).encode("utf-8") if data is not None else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {"error": True, "status": exc.code, "detail": error_body}
    except urllib.error.URLError as exc:
        return {"error": True, "detail": str(exc.reason)}


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def evaluate_action(
    agent_id: str,
    action_type: str,
    tool_name: str = "",
    target: str = "",
    cost_usd: float = 0.0,
) -> dict:
    """Evaluate whether an agent action is allowed by the guardrail policy.

    Args:
        agent_id: Unique identifier of the agent requesting the action.
        action_type: Category of action (e.g. "tool_call", "shell", "http").
        tool_name: Name of the tool being invoked (optional).
        target: Target resource such as a file path or URL (optional).
        cost_usd: Estimated cost of the action in USD (optional).

    Returns:
        Evaluation verdict from the guardrail API.
    """
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "action_type": action_type,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    if target:
        payload["target"] = target
    if cost_usd:
        payload["cost_usd"] = cost_usd

    return _api_call("POST", "/v1/evaluate", payload)


@mcp.tool()
def register_agent(
    name: str,
    framework: str = "",
    description: str = "",
) -> dict:
    """Register a new agent with the guardrail system.

    Args:
        name: Human-readable name for the agent.
        framework: Agent framework (e.g. "langchain", "autogen") (optional).
        description: Short description of the agent's purpose (optional).

    Returns:
        Registered agent record including its assigned ID.
    """
    payload: dict[str, Any] = {"name": name}
    if framework:
        payload["framework"] = framework
    if description:
        payload["description"] = description

    return _api_call("POST", "/v1/agents", payload)


@mcp.tool()
def list_agents() -> dict:
    """List all agents registered in the guardrail system.

    Returns:
        List of agent records.
    """
    return _api_call("GET", "/v1/agents")


@mcp.tool()
def get_stats() -> dict:
    """Retrieve aggregate statistics from the guardrail system.

    Returns:
        Statistics including evaluation counts, agent counts, and policy data.
    """
    return _api_call("GET", "/v1/stats")


@mcp.tool()
def kill_agent(agent_id: str) -> dict:
    """Kill-switch an agent, immediately blocking all its actions.

    Args:
        agent_id: Unique identifier of the agent to kill.

    Returns:
        Confirmation from the guardrail API.
    """
    return _api_call("POST", f"/v1/agents/{agent_id}/kill")


@mcp.tool()
def unkill_agent(agent_id: str) -> dict:
    """Re-enable a previously killed agent.

    Args:
        agent_id: Unique identifier of the agent to reactivate.

    Returns:
        Confirmation from the guardrail API.
    """
    return _api_call("POST", f"/v1/agents/{agent_id}/unkill")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Guardrail MCP Server")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="MCP transport to use (default: streamable-http)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for streamable-http transport (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    if not GUARDRAIL_ENDPOINT:
        print("WARNING: GUARDRAIL_ENDPOINT is not set")
    if not GUARDRAIL_API_KEY:
        print("WARNING: GUARDRAIL_API_KEY is not set")

    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)
    else:
        mcp.run(transport="stdio")
