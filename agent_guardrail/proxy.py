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
    GET  /v1/spend/{agent_id}  -- Spend tracking
    GET  /v1/stats             -- Statistics
    GET  /v1/templates         -- Default policy templates
    GET  /v1/billing/packs              -- Credit pack catalog (public)
    GET  /v1/billing/balance            -- Agent credit balance
    POST /v1/billing/checkout           -- Create BTC payment
    GET  /v1/billing/payment/{id}       -- Poll payment status
    GET  /v1/billing/webhook            -- Blockonomics callback
    POST /v1/billing/stripe/checkout    -- Create Stripe Checkout Session
    POST /v1/billing/stripe/webhook     -- Stripe webhook (signature-verified)
    POST /v1/billing/grant              -- Admin: manual credit grant
    GET  /v1/billing/ledger/{agent_id}  -- Admin: transaction history
    GET  /pricing                       -- Static pricing page
    GET  /health               -- Health check
    GET  /.well-known/agent-card.json -- A2A agent card (public)

Authentication:
    Agents authenticate via X-API-Key header (the key returned at registration).
    Dashboard/admin requests use X-Admin-Key header (set via --admin-key or env).
"""

import argparse
import hmac
import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("guardrail-proxy")


_PRICING_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Guardrail — Pricing</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0a0a0a; color: #e0e0e0; line-height: 1.6; }
  .container { max-width: 900px; margin: 0 auto; padding: 60px 20px; }
  h1 { font-size: 2.4rem; text-align: center; margin-bottom: 8px; }
  .subtitle { text-align: center; color: #888; margin-bottom: 48px; font-size: 1.1rem; }
  .free-tier { background: #111; border: 1px solid #333; border-radius: 12px;
               padding: 24px; text-align: center; margin-bottom: 40px; }
  .free-tier h2 { color: #4ade80; font-size: 1.4rem; margin-bottom: 8px; }
  .packs { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
           gap: 20px; }
  .pack { background: #111; border: 1px solid #333; border-radius: 12px;
          padding: 28px; text-align: center; transition: border-color 0.2s; }
  .pack:hover { border-color: #f7931a; }
  .pack h3 { font-size: 1.2rem; margin-bottom: 12px; }
  .pack .price { font-size: 2rem; font-weight: 700; color: #f7931a; }
  .pack .credits { font-size: 1rem; color: #aaa; margin: 8px 0; }
  .pack .per { font-size: 0.9rem; color: #666; }
  .btc-note { text-align: center; margin-top: 40px; color: #888; font-size: 0.95rem; }
  .btc-note span { color: #f7931a; }
  .api-note { text-align: center; margin-top: 16px; color: #666; font-size: 0.85rem; }
  code { background: #1a1a1a; padding: 2px 6px; border-radius: 4px; font-size: 0.85rem; }
</style>
</head>
<body>
<div class="container">
  <h1>Agent Guardrail</h1>
  <p class="subtitle">Action-level governance for AI agents — pay per evaluation</p>
  <div class="free-tier">
    <h2>Free Tier</h2>
    <p>100 evaluations/day per agent — no credit card, no signup</p>
  </div>
  <div class="packs">
    <div class="pack">
      <h3>Starter</h3>
      <div class="price">$10</div>
      <div class="credits">1,000 evaluations</div>
      <div class="per">$0.010 / eval</div>
    </div>
    <div class="pack">
      <h3>Growth</h3>
      <div class="price">$40</div>
      <div class="credits">5,000 evaluations</div>
      <div class="per">$0.008 / eval</div>
    </div>
    <div class="pack">
      <h3>Scale</h3>
      <div class="price">$150</div>
      <div class="credits">25,000 evaluations</div>
      <div class="per">$0.006 / eval</div>
    </div>
  </div>
  <p class="btc-note"><span>&#x20BF;</span> Bitcoin only — non-custodial, no KYC</p>
  <p class="api-note">Purchase via API: <code>POST /v1/billing/checkout</code></p>
</div>
</body>
</html>
"""


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
    from .billing import BillingManager, CREDIT_PACKS, FREE_TIER_DAILY
    from fastapi.responses import HTMLResponse

    store = GuardrailStore(db_path=db_path)
    engine = PolicyEngine(store)

    # Billing: enabled if either Blockonomics or Stripe key is set
    _billing_api_key = os.environ.get("BLOCKONOMICS_API_KEY", "").strip()
    _stripe_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    billing: Optional[BillingManager] = None
    if _billing_api_key or _stripe_key:
        billing = BillingManager(db_path=store.db_path)
        _providers = []
        if _billing_api_key:
            _providers.append("Blockonomics")
        if _stripe_key:
            _providers.append("Stripe")
        logger.info("Billing enabled (%s)", ", ".join(_providers))
    else:
        logger.info("Billing disabled (no BLOCKONOMICS_API_KEY or STRIPE_SECRET_KEY)")

    _admin_key = (admin_key or os.environ.get("GUARDRAIL_ADMIN_KEY", "")).strip()

    if not _admin_key:
        logger.warning(
            "WARNING: No admin key configured. Admin endpoints are UNPROTECTED. "
            "Set --admin-key or GUARDRAIL_ADMIN_KEY for production use."
        )

    _cors_origins = os.environ.get("GUARDRAIL_CORS_ORIGINS", "").strip()
    cors_origins = (
        [o.strip() for o in _cors_origins.split(",") if o.strip()] if _cors_origins else []
    )

    app = FastAPI(
        title="Agent Guardrail Gateway",
        description="Action-level governance for AI agents",
        version="0.1.1",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Auth helpers --------------------------------------------------

    def _require_agent_or_admin(request: Request) -> Dict[str, Any]:
        """Authenticate via X-API-Key (agent) or X-Admin-Key (admin)."""
        api_key = request.headers.get("X-API-Key", "")
        admin = request.headers.get("X-Admin-Key", "")

        if _admin_key and admin and hmac.compare_digest(admin, _admin_key):
            return {"role": "admin"}

        if api_key:
            agent = store.get_agent_by_key(api_key)
            if agent:
                return {"role": "agent", "agent": agent}

        raise HTTPException(status_code=401, detail="Invalid API key")

    def _require_admin(request: Request) -> None:
        admin = request.headers.get("X-Admin-Key", "")
        if not _admin_key:
            return  # No admin key configured -> open access (warned at startup)
        if not admin or not hmac.compare_digest(admin, _admin_key):
            raise HTTPException(status_code=403, detail="Admin key required")

    # -- Health --------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "agent-guardrail", "version": "0.1.1"}

    # -- Agent Card (A2A discovery) ------------------------------------

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return {
            "name": "Agent Guardrail Gateway",
            "description": (
                "Action-level governance for AI agents. Evaluate actions against "
                "policies before execution. Supports allowlists, denylists, spend "
                "caps, kill switch, and full flight recording."
            ),
            "url": "http://157.230.82.223/guardrail",
            "version": "0.1.1",
            "protocol": "a2a",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "skills": [
                {
                    "name": "evaluate_action",
                    "description": (
                        "Evaluate an agent action against configured policies. "
                        "Returns allow/deny/require_approval with reason and risk score."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "Registered agent ID",
                            },
                            "action_type": {
                                "type": "string",
                                "description": (
                                    "Action category: bash, read_file, write_file, "
                                    "network_request, api_call, delete, install, sudo, "
                                    "exec, spawn_agent"
                                ),
                            },
                            "tool_name": {
                                "type": "string",
                                "description": "Specific tool being invoked",
                            },
                            "target": {
                                "type": "string",
                                "description": "File path, URL, or other target",
                            },
                            "cost_usd": {
                                "type": "number",
                                "description": "Estimated cost of this action in USD",
                            },
                        },
                        "required": ["agent_id", "action_type"],
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["allow", "deny", "require_approval"],
                            },
                            "reason": {"type": "string"},
                            "risk_score": {"type": "number"},
                            "policy_id": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "register_agent",
                    "description": (
                        "Register a new agent in the guardrail system. "
                        "Returns agent ID and API key."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Agent name"},
                            "framework": {
                                "type": "string",
                                "description": (
                                    "Agent framework (langchain, crewai, autogen, etc)"
                                ),
                            },
                            "description": {
                                "type": "string",
                                "description": "Agent description",
                            },
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "agent_stats",
                    "description": (
                        "Get aggregate statistics: active agents, total actions, "
                        "spend, decisions by type."
                    ),
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ],
            "authentication": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "Agent API key (returned at registration) or admin key",
            },
        }

    # -- Core: Evaluate ------------------------------------------------

    class EvaluateRequest(BaseModel):
        agent_id: str = Field(..., min_length=1)
        action_type: str = Field(..., min_length=1)
        tool_name: Optional[str] = None
        target: Optional[str] = None
        cost_usd: float = Field(default=0.0, ge=0.0)
        session_id: Optional[str] = None
        detail: Optional[Dict] = None
        record: bool = True

    @app.post("/v1/evaluate")
    async def evaluate_action(request: Request, body: EvaluateRequest):
        auth = _require_agent_or_admin(request)

        # Agents can only evaluate for themselves
        if auth["role"] == "agent" and auth["agent"]["id"] != body.agent_id:
            raise HTTPException(status_code=403, detail="Cannot evaluate for another agent")

        # Billing: check credits (admin bypasses)
        if billing and billing.enabled and auth["role"] != "admin":
            allowed, reason = billing.check_and_deduct(body.agent_id)
            if not allowed:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "payment_required",
                        "message": "Free tier exhausted and no credits remaining",
                        "billing_url": "/v1/billing/packs",
                    },
                )

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

    # -- Billing -------------------------------------------------------

    @app.get("/v1/billing/packs")
    async def billing_packs():
        """Public: list available credit packs and payment methods."""
        return {
            "packs": list(CREDIT_PACKS.values()),
            "free_tier_daily": FREE_TIER_DAILY,
            "billing_enabled": billing is not None and billing.enabled,
            "payment_methods": {
                "btc": billing is not None
                and bool(os.environ.get("BLOCKONOMICS_API_KEY", "").strip()),
                "stripe": billing is not None and billing.stripe_enabled if billing else False,
            },
        }

    @app.get("/v1/billing/balance")
    async def billing_balance(request: Request):
        """Agent-auth: get own credit balance."""
        auth = _require_agent_or_admin(request)
        if auth["role"] == "agent":
            agent_id = auth["agent"]["id"]
        else:
            agent_id = request.query_params.get("agent_id", "")
            if not agent_id:
                raise HTTPException(400, "agent_id query param required for admin")

        if not billing:
            return {"billing_enabled": False}

        return billing.get_balance(agent_id)

    class CheckoutRequest(BaseModel):
        pack_id: str = Field(..., min_length=1)

    @app.post("/v1/billing/checkout")
    async def billing_checkout(request: Request, body: CheckoutRequest):
        """Agent-auth: create a BTC payment."""
        auth = _require_agent_or_admin(request)
        if auth["role"] == "agent":
            agent_id = auth["agent"]["id"]
        else:
            raise HTTPException(400, "Use agent API key for checkout")

        if not billing:
            raise HTTPException(503, "Billing not configured")

        if body.pack_id not in CREDIT_PACKS:
            raise HTTPException(400, f"Unknown pack: {body.pack_id}")

        try:
            result = billing.create_checkout(agent_id, body.pack_id)
            return result
        except RuntimeError as e:
            logger.error("Checkout failed: %s", e)
            raise HTTPException(502, f"Payment provider error: {e}")

    @app.get("/v1/billing/payment/{payment_id}")
    async def billing_payment_status(request: Request, payment_id: str):
        """Agent-auth: poll payment status."""
        _require_agent_or_admin(request)
        if not billing:
            raise HTTPException(503, "Billing not configured")

        payment = billing.get_payment_status(payment_id)
        if not payment:
            raise HTTPException(404, "Payment not found")
        return payment

    @app.get("/v1/billing/webhook")
    async def billing_webhook(
        request: Request,
        secret: str = "",
        addr: str = "",
        status: int = -1,
        value: int = 0,
        txid: str = "",
    ):
        """Blockonomics callback — unauthenticated, secret-verified."""
        if not billing:
            raise HTTPException(503, "Billing not configured")

        result = billing.handle_webhook(
            addr=addr, status=status, value=value, txid=txid, secret=secret
        )
        if "error" in result:
            if result["error"] == "invalid_secret":
                raise HTTPException(403, "Invalid webhook secret")
            if result["error"] == "unknown_address":
                raise HTTPException(404, "Unknown payment address")

        return result

    # -- Stripe billing ------------------------------------------------

    class StripeCheckoutRequest(BaseModel):
        pack: str = Field(..., min_length=1, description="pack_1000 | pack_5000 | pack_25000")
        agent_id: str = Field(..., min_length=1)

    @app.post("/v1/billing/stripe/checkout")
    async def stripe_checkout(request: Request, body: StripeCheckoutRequest):
        """Agent or admin: create a Stripe Checkout Session.

        Returns ``checkout_url`` (redirect the user there) and ``session_id``.
        """
        # Allow either agent auth (self-service) or admin auth
        auth = _require_agent_or_admin(request)
        if auth["role"] == "agent":
            # Agents may only buy credits for themselves
            if auth["agent"]["id"] != body.agent_id:
                raise HTTPException(403, "Cannot purchase for another agent")

        if not billing:
            raise HTTPException(503, "Billing not configured")

        if not billing.stripe_enabled:
            raise HTTPException(503, "Stripe billing not configured (missing STRIPE_SECRET_KEY)")

        if body.pack not in CREDIT_PACKS:
            raise HTTPException(400, f"Unknown pack: {body.pack}")

        try:
            result = billing.create_stripe_checkout(body.agent_id, body.pack)
            return result
        except ImportError as e:
            logger.error("Stripe package missing: %s", e)
            raise HTTPException(
                503, "stripe package not installed — pip install agent-guardrail[stripe]"
            )
        except RuntimeError as e:
            logger.error("Stripe checkout failed: %s", e)
            raise HTTPException(502, f"Stripe error: {e}")

    @app.post("/v1/billing/stripe/webhook")
    async def stripe_webhook(request: Request):
        """Stripe webhook endpoint — signature-verified, no auth header needed.

        Handles ``checkout.session.completed`` and grants credits automatically.
        Configure this URL in the Stripe dashboard as your webhook endpoint.
        """
        if not billing:
            raise HTTPException(503, "Billing not configured")

        if not billing.stripe_enabled:
            raise HTTPException(503, "Stripe billing not configured")

        payload = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        if not sig_header:
            raise HTTPException(400, "Missing stripe-signature header")

        try:
            result = billing.handle_stripe_webhook(payload, sig_header)
            return result
        except ImportError as e:
            logger.error("Stripe package missing: %s", e)
            raise HTTPException(503, "stripe package not installed")
        except ValueError as e:
            # Signature verification failure
            logger.warning("Stripe webhook signature invalid: %s", e)
            raise HTTPException(400, "Invalid Stripe signature")
        except RuntimeError as e:
            logger.error("Stripe webhook error: %s", e)
            raise HTTPException(502, f"Stripe webhook error: {e}")

    class GrantRequest(BaseModel):
        agent_id: str = Field(..., min_length=1)
        amount: int = Field(..., gt=0)
        reason: str = "admin_grant"

    @app.post("/v1/billing/grant")
    async def billing_grant(request: Request, body: GrantRequest):
        """Admin-only: manually grant credits."""
        _require_admin(request)
        if not billing:
            raise HTTPException(503, "Billing not configured")

        result = billing.grant_credits(body.agent_id, body.amount, body.reason)
        return result

    @app.get("/v1/billing/ledger/{agent_id}")
    async def billing_ledger(request: Request, agent_id: str, limit: int = 100):
        """Admin-only: transaction history for an agent."""
        _require_admin(request)
        if not billing:
            raise HTTPException(503, "Billing not configured")

        entries = billing.get_ledger(agent_id, limit=min(limit, 1000))
        return {"ledger": entries, "count": len(entries)}

    # -- Pricing Page --------------------------------------------------

    @app.get("/pricing", response_class=HTMLResponse)
    async def pricing_page():
        return _PRICING_HTML

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

    _has_btc = bool(os.environ.get("BLOCKONOMICS_API_KEY", "").strip())
    _has_stripe = bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())
    _billing_label: str
    if _has_btc and _has_stripe:
        _billing_label = "enabled (Blockonomics + Stripe)"
    elif _has_btc:
        _billing_label = "enabled (Blockonomics)"
    elif _has_stripe:
        _billing_label = "enabled (Stripe)"
    else:
        _billing_label = "disabled"

    print("\n  Agent Guardrail Gateway")
    print("  =======================")
    print(f"  Port:     {args.port}")
    print(f"  DB:       {args.db or 'default (~/.agent-guardrail/guardrail.db)'}")
    print(
        f"  Admin:    {'configured' if args.admin_key or os.environ.get('GUARDRAIL_ADMIN_KEY') else 'open (no key)'}"
    )
    print(f"  Billing:  {_billing_label}")
    print(f"  Docs:     http://{args.host}:{args.port}/docs")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
