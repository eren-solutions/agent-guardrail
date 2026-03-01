"""
Guardrail Proxy -- Standalone Deployment
========================================

A minimal FastAPI server that exposes the guardrail gateway endpoints.
Deployable independently as a product for any AI agent framework.

Usage:
    guardrail-proxy                                    # Default port 8300
    guardrail-proxy --port 9000                        # Custom port
    guardrail-proxy --db /path/to/guardrail.db

Endpoints:
    POST /v1/evaluate          -- Core: evaluate an action against policies
    POST /v1/agents            -- Register a new agent
    GET  /v1/agents            -- List agents
    POST /v1/agents/{id}/kill  -- Emergency kill switch
    POST /v1/agents/{id}/unkill
    GET  /v1/policies          -- List policies
    POST /v1/policies          -- Create a policy
    GET  /v1/actions           -- Flight recorder entries
    GET  /v1/actions/replay/{session_id}
    GET  /v1/approvals         -- Pending approvals
    POST /v1/approvals/{id}/decide
    GET  /v1/stats             -- Statistics
    GET  /v1/templates         -- Default policy templates
    GET  /health               -- Health check

Authentication:
    Agents authenticate via X-API-Key header (the key returned at registration).
    Dashboard/admin requests use X-Admin-Key header (set via --admin-key or env).
"""

import argparse
import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("guardrail-proxy")


def create_app(db_path: Optional[str] = None, admin_key: Optional[str] = None):
    """Create a standalone FastAPI app for the Guardrail Gateway."""
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel, Field
    except ImportError:
        print("FastAPI is required: pip install agent-guardrail[proxy]")
        sys.exit(1)

    from .store import GuardrailStore
    from .policy import PolicyEngine, DEFAULT_POLICIES

    store = GuardrailStore(db_path=db_path)
    engine = PolicyEngine(store)

    _admin_key = admin_key or os.environ.get("GUARDRAIL_ADMIN_KEY", "")

    app = FastAPI(
        title="Agent Guardrail Gateway",
        description="Action-level governance for AI agents",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Auth helpers --------------------------------------------------

    def _require_agent_or_admin(request: Request) -> Dict[str, Any]:
        """Authenticate via X-API-Key (agent) or X-Admin-Key (admin)."""
        api_key = request.headers.get("X-API-Key", "")
        admin = request.headers.get("X-Admin-Key", "")

        if _admin_key and admin == _admin_key:
            return {"role": "admin"}

        if api_key:
            agent = store.get_agent_by_key(api_key)
            if agent:
                return {"role": "agent", "agent": agent}

        raise HTTPException(status_code=401, detail="Invalid API key")

    def _require_admin(request: Request) -> None:
        admin = request.headers.get("X-Admin-Key", "")
        if not _admin_key:
            return  # No admin key configured -> open access
        if admin != _admin_key:
            raise HTTPException(status_code=403, detail="Admin key required")

    # -- Health --------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "agent-guardrail", "version": "0.1.0"}

    # -- Core: Evaluate ------------------------------------------------

    class EvaluateRequest(BaseModel):
        agent_id: str = Field(..., min_length=1)
        action_type: str = Field(..., min_length=1)
        tool_name: Optional[str] = None
        target: Optional[str] = None
        cost_usd: float = 0.0
        session_id: Optional[str] = None
        detail: Optional[Dict] = None
        record: bool = True

    @app.post("/v1/evaluate")
    async def evaluate_action(request: Request, body: EvaluateRequest):
        auth = _require_agent_or_admin(request)

        # Agents can only evaluate for themselves
        if auth["role"] == "agent" and auth["agent"]["id"] != body.agent_id:
            raise HTTPException(status_code=403, detail="Cannot evaluate for another agent")

        if body.record:
            decision = engine.evaluate_and_record(
                agent_id=body.agent_id,
                action_type=body.action_type,
                tool_name=body.tool_name,
                target=body.target,
                cost_usd=body.cost_usd,
                session_id=body.session_id,
                detail=body.detail,
            )
        else:
            decision = engine.evaluate(
                agent_id=body.agent_id,
                action_type=body.action_type,
                tool_name=body.tool_name,
                target=body.target,
                cost_usd=body.cost_usd,
                detail=body.detail,
            )

        result = {
            "decision": decision.decision,
            "reason": decision.reason,
            "risk_score": decision.risk_score,
            "policy_id": decision.policy_id,
        }
        if decision.metadata:
            result["metadata"] = decision.metadata
        return result

    # -- Agents --------------------------------------------------------

    class AgentCreate(BaseModel):
        name: str = Field(..., min_length=1)
        framework: str = ""
        description: str = ""

    @app.post("/v1/agents")
    async def register_agent(request: Request, body: AgentCreate):
        _require_admin(request)
        result = store.register_agent(
            name=body.name, framework=body.framework, description=body.description
        )
        return {"agent": result, "created": True}

    @app.get("/v1/agents")
    async def list_agents(request: Request):
        _require_admin(request)
        agents = store.list_agents()
        # Strip API keys from list response
        for a in agents:
            a["api_key"] = a["api_key"][:12] + "..."
        return {"agents": agents, "count": len(agents)}

    @app.post("/v1/agents/{agent_id}/kill")
    async def kill_agent(request: Request, agent_id: str):
        _require_admin(request)
        ok = store.kill_agent(agent_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Agent not found")
        logger.warning("KILL SWITCH activated for agent %s", agent_id)
        return {"killed": True, "agent_id": agent_id}

    @app.post("/v1/agents/{agent_id}/unkill")
    async def unkill_agent(request: Request, agent_id: str):
        _require_admin(request)
        ok = store.unkill_agent(agent_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"unkilled": True, "agent_id": agent_id}

    # -- Policies ------------------------------------------------------

    class PolicyCreate(BaseModel):
        name: str = Field(..., min_length=1)
        description: str = ""
        agent_id: Optional[str] = None
        scope: str = "global"
        priority: int = 100
        rules: Dict = Field(default_factory=dict)

    @app.get("/v1/policies")
    async def list_policies(request: Request, agent_id: Optional[str] = None):
        _require_admin(request)
        policies = store.get_policies(agent_id=agent_id)
        return {"policies": policies, "count": len(policies)}

    @app.post("/v1/policies")
    async def create_policy(request: Request, body: PolicyCreate):
        _require_admin(request)
        pid = store.save_policy(body.model_dump())
        return {"policy_id": pid, "created": True}

    @app.delete("/v1/policies/{policy_id}")
    async def delete_policy(request: Request, policy_id: str):
        _require_admin(request)
        store.delete_policy(policy_id)
        return {"deleted": True}

    @app.get("/v1/templates")
    async def list_templates():
        return {"templates": DEFAULT_POLICIES}

    # -- Flight Recorder -----------------------------------------------

    @app.get("/v1/actions")
    async def list_actions(
        request: Request,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        decision: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        _require_admin(request)
        actions = store.list_actions(
            agent_id=agent_id,
            session_id=session_id,
            decision=decision,
            limit=min(limit, 500),
            offset=offset,
        )
        return {"actions": actions, "count": len(actions)}

    @app.get("/v1/actions/replay/{session_id}")
    async def session_replay(request: Request, session_id: str):
        _require_admin(request)
        actions = store.get_session_replay(session_id)
        total_cost = sum(a.get("cost_usd", 0) for a in actions)
        denied = sum(1 for a in actions if a.get("decision") == "deny")
        return {
            "session_id": session_id,
            "actions": actions,
            "count": len(actions),
            "total_cost_usd": round(total_cost, 4),
            "denied_count": denied,
        }

    # -- Approvals -----------------------------------------------------

    @app.get("/v1/approvals")
    async def list_approvals(request: Request, agent_id: Optional[str] = None):
        _require_admin(request)
        approvals = store.list_pending_approvals(agent_id=agent_id)
        return {"approvals": approvals, "count": len(approvals)}

    class ApprovalDecision(BaseModel):
        approved: bool
        decided_by: str = "admin"

    @app.post("/v1/approvals/{approval_id}/decide")
    async def decide_approval(request: Request, approval_id: str, body: ApprovalDecision):
        _require_admin(request)
        ok = store.decide_approval(approval_id, approved=body.approved, decided_by=body.decided_by)
        if not ok:
            raise HTTPException(status_code=404, detail="Approval not found")
        return {"decided": True, "result": "approved" if body.approved else "denied"}

    # -- Spend + Stats -------------------------------------------------

    @app.get("/v1/spend/{agent_id}")
    async def get_spend(request: Request, agent_id: str, period: Optional[str] = None):
        _require_admin(request)
        spend = store.get_spend(agent_id, period=period)
        total = store.get_total_spend(agent_id)
        return {"spend": spend, "total_spend_usd": round(total, 4)}

    @app.get("/v1/stats")
    async def get_stats(request: Request):
        _require_admin(request)
        return {"stats": store.stats()}

    return app


def main():
    parser = argparse.ArgumentParser(
        prog="guardrail-proxy",
        description="Agent Guardrail -- standalone proxy server",
    )
    parser.add_argument("--port", type=int, default=8300, help="Port (default 8300)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--admin-key", help="Admin API key (or GUARDRAIL_ADMIN_KEY env)")
    parser.add_argument("--log-level", default="info", help="Log level")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required: pip install agent-guardrail[proxy]")
        sys.exit(1)

    app = create_app(db_path=args.db, admin_key=args.admin_key)

    print("\n  Agent Guardrail Gateway")
    print("  =======================")
    print(f"  Port:     {args.port}")
    print(f"  DB:       {args.db or 'default (~/.agent-guardrail/guardrail.db)'}")
    print(
        f"  Admin:    {'configured' if args.admin_key or os.environ.get('GUARDRAIL_ADMIN_KEY') else 'open (no key)'}"
    )
    print(f"  Docs:     http://{args.host}:{args.port}/docs")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
