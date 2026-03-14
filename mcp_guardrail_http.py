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
from typing import Any, Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GUARDRAIL_ENDPOINT = os.environ.get("GUARDRAIL_ENDPOINT", "")
GUARDRAIL_API_KEY = os.environ.get("GUARDRAIL_API_KEY", "")
DEFAULT_PORT = int(os.environ.get("PORT", "8200"))

mcp = FastMCP(
    "Agent Guardrail",
    instructions=(
        "Agent Guardrail enforces action-level policies for AI agents. "
        "Use evaluate_action before performing any tool call, shell command, "
        "or HTTP request to check if the action is permitted. Register agents "
        "first with register_agent, then evaluate their actions. Use kill_agent "
        "for emergency shutdowns."
    ),
)

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
# MCP tools — with full parameter descriptions and annotations
# ---------------------------------------------------------------------------


@mcp.tool(annotations={
    "title": "Evaluate Action",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def evaluate_action(
    agent_id: Annotated[str, Field(description="Unique identifier of the agent. Must be registered first via register_agent.")],
    action_type: Annotated[str, Field(description="Category of action: tool_call, shell, http, file_read, file_write, database, api_call.")],
    tool_name: Annotated[str, Field(description="Name of the tool being invoked, e.g. bash, write_file, curl.")] = "",
    target: Annotated[str, Field(description="Target resource path or URL, e.g. /etc/passwd, https://api.example.com.")] = "",
    cost_usd: Annotated[float, Field(description="Estimated cost in USD for spend tracking and budget enforcement.")] = 0.0,
) -> dict:
    """Evaluate whether an agent action is allowed by the guardrail policy. Call this BEFORE executing any tool, shell command, or HTTP request."""
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


@mcp.tool(annotations={
    "title": "Register Agent",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
})
def register_agent(
    name: Annotated[str, Field(description="Human-readable name for the agent, e.g. code-reviewer, data-analyst.")],
    framework: Annotated[str, Field(description="Agent framework: langchain, autogen, crewai, claude-code.")] = "",
    description: Annotated[str, Field(description="Short description of what this agent does.")] = "",
) -> dict:
    """Register a new agent with the guardrail system. Must be called before evaluate_action."""
    payload: dict[str, Any] = {"name": name}
    if framework:
        payload["framework"] = framework
    if description:
        payload["description"] = description

    return _api_call("POST", "/v1/agents", payload)


@mcp.tool(annotations={
    "title": "List Agents",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def list_agents() -> dict:
    """List all agents currently registered in the guardrail system. Shows active and killed agents.

    Returns:
        Array of agent records with fields: id, name, framework, description, status, created_at.
    """
    return _api_call("GET", "/v1/agents")


@mcp.tool(annotations={
    "title": "Get Statistics",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def get_stats() -> dict:
    """Retrieve aggregate statistics from the guardrail system including evaluation counts, policy data, and agent metrics.

    Returns:
        Statistics object with fields: total_evaluations, allowed_count, denied_count, agent_count, policy_count.
    """
    return _api_call("GET", "/v1/stats")


@mcp.tool(annotations={
    "title": "Kill Agent (Emergency)",
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
})
def kill_agent(
    agent_id: Annotated[str, Field(description="Unique identifier of the agent to kill. All subsequent actions will be denied.")],
) -> dict:
    """Emergency kill-switch: immediately block ALL actions for an agent. Use when an agent is misbehaving or compromised."""
    return _api_call("POST", f"/v1/agents/{agent_id}/kill")


@mcp.tool(annotations={
    "title": "Unkill Agent",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def unkill_agent(
    agent_id: Annotated[str, Field(description="Unique identifier of the agent to reactivate.")],
) -> dict:
    """Re-enable a previously killed agent, restoring its ability to have actions evaluated."""
    return _api_call("POST", f"/v1/agents/{agent_id}/unkill")


# ---------------------------------------------------------------------------
# MCP prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def setup_guardrail() -> str:
    """Step-by-step guide to set up guardrail enforcement for a new agent."""
    return (
        "To set up Agent Guardrail for your agent:\n\n"
        "1. Register your agent: call register_agent with a name and description\n"
        "2. Save the returned agent_id\n"
        "3. Before EVERY action (tool call, shell command, HTTP request), "
        "call evaluate_action with your agent_id and action details\n"
        "4. Only proceed if the evaluation returns allowed=true\n"
        "5. If denied, respect the policy — do not retry the same action\n\n"
        "Emergency: call kill_agent to immediately block all actions for an agent."
    )


@mcp.prompt()
def security_best_practices() -> str:
    """Security best practices for AI agent governance."""
    return (
        "Agent Security Best Practices:\n\n"
        "1. Always evaluate before executing — never skip the guardrail check\n"
        "2. Use specific action_types: 'shell', 'file_write', 'http' not generic 'action'\n"
        "3. Include target paths/URLs so policies can match specific resources\n"
        "4. Track costs with cost_usd for budget enforcement\n"
        "5. Monitor get_stats regularly for anomalous patterns\n"
        "6. Use kill_agent immediately if an agent shows unexpected behavior\n"
        "7. Register agents with descriptive names and framework info for audit trails"
    )


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
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = args.port
        mcp.settings.transport_security.enable_dns_rebinding_protection = False
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
