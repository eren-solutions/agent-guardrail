# Agent Guardrail

**Action-level governance for AI agents — control what they DO, not what they SAY.**

[![PyPI](https://img.shields.io/pypi/v/agent-guardrail)](https://pypi.org/project/agent-guardrail/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## The Problem

AI agents are getting tool access. They can run shell commands, make API calls, read files, spend money. But most "guardrails" only filter what agents *say* — not what they *do*.

Real incidents:
- **AutoGPT** autonomously spent $10K+ on API calls in a single session
- **ChaosGPT** attempted to access military systems and recruit other AI agents
- **Air Canada chatbot** invented a refund policy that cost the airline $800+

You need action-level control. Not output filtering.

## What Agent Guardrail Does

```
Agent Framework --> Agent Guardrail --> {allow, deny, require_approval}
                                    --> Flight Recorder logs everything
```

- **Policy Engine** — allowlists, denylists, glob patterns for tools and targets
- **Spend Caps** — daily and total USD limits per agent
- **Kill Switch** — instantly deny all actions for a runaway agent
- **Flight Recorder** — every action logged with full replay capability
- **Approval Gates** — route risky actions to human review
- **Risk Scoring** — automatic risk assessment per action type
- **3 Templates** — restrictive, moderate, permissive (apply in one command)
- **Pay-per-eval Billing** — free tier + BTC credit packs via Blockonomics

**Zero dependencies.** Python stdlib only. SQLite for storage.

## 30-Second Quickstart

```bash
pip install agent-guardrail

# Register an agent
agent-guardrail register "my-research-agent" --framework langchain

# Apply the moderate policy template
agent-guardrail apply-template moderate <agent-id>

# Test it
agent-guardrail eval <agent-id> bash --target /workspace/test.sh     # -> allow
agent-guardrail eval <agent-id> bash --target /etc/shadow             # -> deny
agent-guardrail eval <agent-id> sudo                                  # -> deny
```

## Python API

```python
from agent_guardrail import GuardrailStore, PolicyEngine, DEFAULT_POLICIES

# Initialize
store = GuardrailStore()  # ~/.agent-guardrail/guardrail.db
engine = PolicyEngine(store)

# Register agent
agent = store.register_agent("my-agent", framework="langchain")

# Apply policy template
store.save_policy({
    "name": "moderate",
    "agent_id": agent["id"],
    "rules": DEFAULT_POLICIES["moderate"]["rules"],
})

# Evaluate actions
decision = engine.evaluate(agent["id"], "bash", target="/workspace/run.sh")
# -> PolicyDecision(decision="allow", risk_score=0.7)

decision = engine.evaluate(agent["id"], "bash", target="/etc/shadow")
# -> PolicyDecision(decision="deny", reason="Target '/etc/shadow' is denied...")

# Evaluate + record to flight recorder
decision = engine.evaluate_and_record(
    agent_id=agent["id"],
    action_type="api_call",
    tool_name="openai_chat",
    cost_usd=0.05,
    session_id="session-123",
)
```

## Framework Integrations

### LangChain Callback

```python
from agent_guardrail import GuardrailStore, PolicyEngine

class GuardrailCallback:
    """Drop into any LangChain agent as a callback handler."""
    def __init__(self, agent_id, db_path=None):
        self._engine = PolicyEngine(GuardrailStore(db_path=db_path))
        self.agent_id = agent_id

    def on_tool_start(self, serialized, input_str, **kwargs):
        decision = self._engine.evaluate_and_record(
            agent_id=self.agent_id,
            action_type="tool_call",
            tool_name=serialized.get("name"),
            target=input_str[:200],
        )
        if decision.decision == "deny":
            raise PermissionError(f"Guardrail: {decision.reason}")
```

### CrewAI Task Guardrail

```python
from agent_guardrail import GuardrailStore, PolicyEngine

def make_guardrail(agent_id, db_path=None):
    engine = PolicyEngine(GuardrailStore(db_path=db_path))

    def check(task_output):
        decision = engine.evaluate_and_record(
            agent_id=agent_id, action_type="task_output",
            target=str(task_output)[:200],
        )
        if decision.decision == "deny":
            return (False, f"Blocked: {decision.reason}")
        return (True, task_output)
    return check

# task = Task(description="...", guardrail=make_guardrail("agent-id"))
```

### Universal Decorator

```python
from agent_guardrail import GuardrailStore, PolicyEngine
import functools

def guardrail(agent_id, action_type="function_call", db_path=None):
    engine = PolicyEngine(GuardrailStore(db_path=db_path))
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            target = str(args[0])[:200] if args else None
            decision = engine.evaluate_and_record(
                agent_id=agent_id, action_type=action_type,
                tool_name=func.__name__, target=target,
            )
            if decision.decision == "deny":
                raise PermissionError(f"Guardrail: {decision.reason}")
            return func(*args, **kwargs)
        return wrapper
    return decorator

@guardrail("my-agent", action_type="bash")
def run_command(cmd):
    ...
```

## Hosted API (For Agents)

The library is for humans. The API is for agents.

An orchestrator running 5 sub-agents doesn't `pip install` — it calls an endpoint.

```bash
# Start the proxy server
pip install agent-guardrail[proxy]
guardrail-proxy --port 8300 --admin-key YOUR_ADMIN_KEY
```

```bash
# Register an agent (admin)
curl -X POST http://localhost:8300/v1/agents \
  -H "X-Admin-Key: YOUR_ADMIN_KEY" \
  -d '{"name": "research-agent", "framework": "crewai"}'

# Evaluate an action (agent)
curl -X POST http://localhost:8300/v1/evaluate \
  -H "X-API-Key: gw_agent_key_here" \
  -d '{
    "agent_id": "...",
    "action_type": "bash",
    "tool_name": "shell",
    "target": "/etc/shadow",
    "cost_usd": 0.0
  }'
# -> {"decision": "deny", "reason": "Target denied...", "risk_score": 0.7}
```

Full API docs at `http://localhost:8300/docs` (Swagger UI).

## Billing & Pricing

Free tier included. Pay with Bitcoin when you need more.

| Tier | Evaluations | Price | Per Eval |
|------|-------------|-------|----------|
| **Free** | 100/day per agent | $0 | $0 |
| **Starter** | 1,000 | $10 | $0.010 |
| **Growth** | 5,000 | $40 | $0.008 |
| **Scale** | 25,000 | $150 | $0.006 |

Credits are prepaid and never expire. Admin-authenticated requests bypass billing entirely.

**How it works:**

```bash
# Check your balance
curl http://localhost:8300/v1/billing/balance \
  -H "X-API-Key: gw_your_agent_key"

# Buy credits (returns a BTC address + amount)
curl -X POST http://localhost:8300/v1/billing/checkout \
  -H "X-API-Key: gw_your_agent_key" \
  -d '{"pack_id": "pack_1000"}'
# -> {"btc_address": "bc1q...", "amount_btc": 0.00015, "amount_satoshi": 15000, ...}

# Pay the BTC address -> webhook confirms -> credits granted automatically
```

When free tier is exhausted and no credits remain, `/v1/evaluate` returns **402 Payment Required** with a link to available packs.

**Self-hosted billing:** Set `BLOCKONOMICS_API_KEY` and `BLOCKONOMICS_WEBHOOK_SECRET` environment variables. Without these, billing is disabled and all evaluations proceed without metering (backward compatible).

## Policy Rules Reference

```python
{
    "tool_allowlist": ["read_file", "write_file"],    # Only these tools allowed
    "tool_denylist": ["sudo", "rm", "delete*"],       # These tools always denied
    "target_allowlist": ["/workspace/*"],              # Only these targets allowed
    "target_denylist": ["/etc/*", "*.env", "*.key"],   # These targets always denied
    "network_allowlist": ["api.openai.com"],           # Allowed network targets
    "network_denylist": ["*"],                         # Denied network targets
    "spend_cap_daily_usd": 25.0,                      # Daily spend limit
    "spend_cap_total_usd": 500.0,                     # Lifetime spend limit
    "require_approval": ["bash", "install"],           # Human approval required
    "risk_threshold": 0.8,                             # Auto-approval gate
}
```

Patterns support glob matching (`*`, `?`, `[abc]`).

## Decision Flow

```
Kill switch? ──deny──> DENY
      |
Agent enabled? ──no──> DENY
      |
Spend cap? ──exceeded──> DENY
      |
Tool denylist? ──match──> DENY
      |
Target denylist? ──match──> DENY
      |
Approval required? ──match──> REQUIRE_APPROVAL
      |
Risk threshold? ──exceeded──> REQUIRE_APPROVAL
      |
Tool allowlist? ──not in list──> DENY
      |
Target allowlist? ──not in list──> DENY
      |
DEFAULT ──> ALLOW
```

## Architecture

```
+-------------------+     +------------------+     +-----------------+
|  Agent Framework  |---->|  Billing Check   |---->|  Policy Engine  |
|  (LangChain,     |     |  (free tier /    |     |  (evaluate)     |
|   CrewAI, custom) |     |   credits)       |     +-----------------+
+-------------------+     +------------------+            |
                                 |                        v
                                 |           +------------------------+
                          402 if empty       |  Decision:             |
                                             |  allow / deny /        |
                                             |  require_approval      |
                                             +------------------------+
                                                         |
                                                         v
                                             +-----------------+
                                             |  Flight Recorder|
                                             |  (SQLite)       |
                                             +-----------------+

+-------------------+     +------------------+
|  BTC Payment      |---->|  Blockonomics    |
|  (checkout)       |     |  (xpub-derived   |
+-------------------+     |   addresses)     |
                          +------------------+
                                 |
                          webhook (status=2)
                                 |
                                 v
                          +------------------+
                          |  Credit Grant    |
                          |  (billing_ledger)|
                          +------------------+
```

## Comparison

| Feature | Agent Guardrail | Guardrails AI | NeMo Guardrails | DIY |
|---------|:-:|:-:|:-:|:-:|
| Action-level control | Yes | No (output only) | No (dialogue only) | Manual |
| Spend caps | Yes | No | No | Manual |
| Kill switch | Yes | No | No | Manual |
| Flight recorder | Yes | No | No | Manual |
| Pay-per-eval billing | Yes (BTC) | No | No | Manual |
| Zero dependencies | Yes | No (many) | No (many) | Varies |
| Framework agnostic | Yes | LangChain-focused | LangChain-focused | Yes |
| Hosted API | Yes | Cloud only | No | Manual |

## CLI Reference

```
agent-guardrail agents                      # List registered agents
agent-guardrail register "name"             # Register a new agent
agent-guardrail kill <agent_id>             # Emergency kill switch
agent-guardrail unkill <agent_id>           # Revoke kill switch
agent-guardrail policies                    # List policies
agent-guardrail apply-template <template> <agent_id>
agent-guardrail actions [--agent X] [--decision deny]
agent-guardrail replay <session_id>         # Session replay
agent-guardrail approvals                   # Pending approvals
agent-guardrail approve <id>                # Approve action
agent-guardrail deny <id>                   # Deny action
agent-guardrail eval <agent_id> <type> [--target X] [--cost 0.5]
agent-guardrail stats                       # Statistics
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GUARDRAIL_DB` | `~/.agent-guardrail/guardrail.db` | SQLite database path |
| `GUARDRAIL_LOG_DIR` | `~/.agent-guardrail/logs` | CLI log directory |
| `GUARDRAIL_ADMIN_KEY` | (none) | Admin API key for proxy |
| `BLOCKONOMICS_API_KEY` | (none) | Blockonomics Store API key (enables billing) |
| `BLOCKONOMICS_WEBHOOK_SECRET` | (none) | Secret for webhook verification |
| `GUARDRAIL_BILLING_ENABLED` | `true` | Set `false` to disable billing even with API key |

## License

MIT
