"""
Guardrail CLI
=============

Standalone CLI for Agent Guardrail operations.
Works outside FastAPI -- no async, no server deps.

Usage:
    agent-guardrail agents                      # List registered agents
    agent-guardrail register "My Agent"          # Register a new agent
    agent-guardrail kill <agent_id>              # Emergency kill switch
    agent-guardrail unkill <agent_id>            # Revoke kill switch
    agent-guardrail policies                     # List policies
    agent-guardrail apply-template moderate <id> # Apply a policy template
    agent-guardrail actions                      # Recent flight recorder entries
    agent-guardrail replay <session_id>          # Replay a session
    agent-guardrail approvals                    # Pending approvals
    agent-guardrail approve <approval_id>        # Approve an action
    agent-guardrail deny <approval_id>           # Deny an action
    agent-guardrail eval <agent_id> <action>     # Test-evaluate an action
    agent-guardrail stats                        # Print stats
"""

import argparse
import json
import logging
import os
import sys
from typing import Optional

LOG_DIR = os.path.expanduser(
    os.environ.get("GUARDRAIL_LOG_DIR", "~/.agent-guardrail/logs")
)
LOG_FILE = os.path.join(LOG_DIR, "guardrail-cli.log")


def _setup_logging(verbose: bool = False) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stderr),
        ],
    )


def cmd_agents(args) -> None:
    """List registered agents."""
    from .store import GuardrailStore

    store = GuardrailStore()
    agents = store.list_agents()

    if not agents:
        print("  No agents registered.")
        return

    print(f"\n  {'NAME':20}  {'FRAMEWORK':12}  {'ENABLED':8}  {'KILLED':7}  {'ID'}")
    print(f"  {chr(9472) * 20}  {chr(9472) * 12}  {chr(9472) * 8}  {chr(9472) * 7}  {chr(9472) * 36}")
    for a in agents:
        name = a.get("name", "?")[:20]
        fw = (a.get("framework") or "-")[:12]
        enabled = "yes" if a.get("enabled") else "no"
        killed = "KILLED" if a.get("killed") else "-"
        print(f"  {name:20}  {fw:12}  {enabled:^8}  {killed:^7}  {a['id']}")

    print(f"\n  {len(agents)} agents")


def cmd_register(args) -> None:
    """Register a new agent."""
    from .store import GuardrailStore

    store = GuardrailStore()
    result = store.register_agent(
        name=args.name,
        framework=args.framework or "",
        description=args.description or "",
    )
    print(f"\n  Agent registered:")
    print(f"    ID:      {result['id']}")
    print(f"    API Key: {result['api_key']}")
    print(f"\n  Save the API key -- it won't be shown again.")


def cmd_kill(args) -> None:
    """Activate kill switch."""
    from .store import GuardrailStore

    store = GuardrailStore()
    ok = store.kill_agent(args.agent_id)
    if ok:
        print(f"  Kill switch ACTIVATED for {args.agent_id}")
    else:
        print(f"  Failed -- agent not found?")


def cmd_unkill(args) -> None:
    """Revoke kill switch."""
    from .store import GuardrailStore

    store = GuardrailStore()
    ok = store.unkill_agent(args.agent_id)
    if ok:
        print(f"  Kill switch REVOKED for {args.agent_id}")
    else:
        print(f"  Failed -- agent not found?")


def cmd_policies(args) -> None:
    """List active policies."""
    from .store import GuardrailStore

    store = GuardrailStore()
    policies = store.get_policies()

    if not policies:
        print("  No policies configured.")
        return

    print(f"\n  {'NAME':30}  {'SCOPE':8}  {'PRIORITY':8}  {'AGENT':12}  {'ID'}")
    print(f"  {chr(9472) * 30}  {chr(9472) * 8}  {chr(9472) * 8}  {chr(9472) * 12}  {chr(9472) * 36}")
    for p in policies:
        name = p.get("name", "?")[:30]
        scope = p.get("scope", "?")[:8]
        priority = str(p.get("priority", "?"))[:8]
        agent = (p.get("agent_id") or "global")[:12]
        print(f"  {name:30}  {scope:8}  {priority:8}  {agent:12}  {p['id']}")

    print(f"\n  {len(policies)} policies")


def cmd_apply_template(args) -> None:
    """Apply a default policy template."""
    from .store import GuardrailStore
    from .policy import DEFAULT_POLICIES

    if args.template not in DEFAULT_POLICIES:
        print(f"  Unknown template: {args.template}")
        print(f"  Available: {', '.join(DEFAULT_POLICIES.keys())}")
        sys.exit(1)

    template = DEFAULT_POLICIES[args.template]
    store = GuardrailStore()
    policy = {
        "name": template["name"],
        "description": template.get("description", ""),
        "agent_id": args.agent_id if args.agent_id != "global" else None,
        "scope": "agent" if args.agent_id != "global" else "global",
        "priority": 50,
        "rules": template["rules"],
    }
    pid = store.save_policy(policy)
    print(f"  Applied '{args.template}' template -> policy {pid}")


def cmd_actions(args) -> None:
    """List recent flight recorder entries."""
    from .store import GuardrailStore

    store = GuardrailStore()
    actions = store.list_actions(
        agent_id=args.agent,
        decision=args.decision,
        limit=args.limit,
    )

    if not actions:
        print("  No actions recorded.")
        return

    print(f"\n  {'TIME':19}  {'DECISION':10}  {'TYPE':15}  {'TOOL':15}  {'TARGET'}")
    print(f"  {chr(9472) * 19}  {chr(9472) * 10}  {chr(9472) * 15}  {chr(9472) * 15}  {chr(9472) * 30}")
    for a in actions:
        time = (a.get("created_at") or "")[:19]
        decision = a.get("decision", "?")[:10]
        atype = a.get("action_type", "?")[:15]
        tool = (a.get("tool_name") or "-")[:15]
        target = (a.get("target") or "-")[:40]

        # Color decision
        if decision.startswith("deny"):
            decision_display = f"\033[31m{decision:10}\033[0m"
        elif decision.startswith("require"):
            decision_display = f"\033[33m{decision:10}\033[0m"
        else:
            decision_display = f"\033[32m{decision:10}\033[0m"

        print(f"  {time}  {decision_display}  {atype:15}  {tool:15}  {target}")

    print(f"\n  {len(actions)} actions shown")


def cmd_replay(args) -> None:
    """Replay a session's actions chronologically."""
    from .store import GuardrailStore

    store = GuardrailStore()
    actions = store.get_session_replay(args.session_id)

    if not actions:
        print(f"  No actions found for session {args.session_id}")
        return

    print(f"\n  Session Replay: {args.session_id}")
    print(f"  {len(actions)} actions\n")

    for i, a in enumerate(actions, 1):
        decision = a.get("decision", "?")
        time = (a.get("created_at") or "")[:19]
        tool = a.get("tool_name") or a.get("action_type", "?")
        target = a.get("target") or "-"
        reason = a.get("decision_reason") or ""
        risk = a.get("risk_score", 0)

        marker = "+" if decision == "allow" else ("-" if decision == "deny" else "?")
        print(f"  {i:3}. [{time}] {marker} {tool} -> {target}")
        if reason:
            print(f"       {reason} (risk: {risk:.2f})")

    cost_total = sum(a.get("cost_usd", 0) for a in actions)
    denied = sum(1 for a in actions if a.get("decision") == "deny")
    print(f"\n  Total cost: ${cost_total:.4f} | Denied: {denied}/{len(actions)}")


def cmd_approvals(args) -> None:
    """List pending approvals."""
    from .store import GuardrailStore

    store = GuardrailStore()
    approvals = store.list_pending_approvals(agent_id=args.agent)

    if not approvals:
        print("  No pending approvals.")
        return

    print(f"\n  {'AGENT':12}  {'TYPE':15}  {'CREATED':19}  {'EXPIRES':19}  {'ID'}")
    print(f"  {chr(9472) * 12}  {chr(9472) * 15}  {chr(9472) * 19}  {chr(9472) * 19}  {chr(9472) * 36}")
    for ap in approvals:
        agent = ap.get("agent_id", "?")[:12]
        atype = ap.get("action_type", "?")[:15]
        created = (ap.get("created_at") or "")[:19]
        expires = (ap.get("expires_at") or "-")[:19]
        print(f"  {agent:12}  {atype:15}  {created}  {expires}  {ap['id']}")

        detail = ap.get("action_detail", {})
        if detail:
            print(f"       Detail: {json.dumps(detail)[:80]}")

    print(f"\n  {len(approvals)} pending")


def cmd_approve(args) -> None:
    """Approve a pending action."""
    from .store import GuardrailStore

    store = GuardrailStore()
    ok = store.decide_approval(args.approval_id, approved=True, decided_by="cli")
    print(f"  {'Approved' if ok else 'Failed'}: {args.approval_id}")


def cmd_deny_approval(args) -> None:
    """Deny a pending action."""
    from .store import GuardrailStore

    store = GuardrailStore()
    ok = store.decide_approval(args.approval_id, approved=False, decided_by="cli")
    print(f"  {'Denied' if ok else 'Failed'}: {args.approval_id}")


def cmd_eval(args) -> None:
    """Test-evaluate an action against policies (dry run)."""
    from .store import GuardrailStore
    from .policy import PolicyEngine

    store = GuardrailStore()
    engine = PolicyEngine(store)

    decision = engine.evaluate(
        agent_id=args.agent_id,
        action_type=args.action_type,
        tool_name=args.tool,
        target=args.target,
        cost_usd=args.cost or 0.0,
    )

    print(f"\n  Evaluation Result:")
    print(f"    Decision:  {decision.decision}")
    print(f"    Reason:    {decision.reason}")
    print(f"    Risk:      {decision.risk_score:.2f}")
    if decision.policy_id:
        print(f"    Policy ID: {decision.policy_id}")


def cmd_stats(args) -> None:
    """Print store statistics."""
    from .store import GuardrailStore

    store = GuardrailStore()
    s = store.stats()

    print("\n  Agent Guardrail -- Stats")
    print(f"  {chr(9472) * 35}")
    print(f"  Active agents:      {s['active_agents']}")
    print(f"  Killed agents:      {s['killed_agents']}")
    print(f"  Active policies:    {s['active_policies']}")
    print(f"  Total actions:      {s['total_actions']}")
    print(f"  Today's actions:    {s['today_actions']}")
    print(f"  Today's spend:      ${s['today_spend_usd']:.4f}")
    print(f"  All-time spend:     ${s['total_spend_usd']:.4f}")
    print(f"  Pending approvals:  {s['pending_approvals']}")

    by_decision = s.get("actions_by_decision", {})
    if by_decision:
        print(f"\n  Actions by decision:")
        for dec, cnt in by_decision.items():
            print(f"    {dec:20} {cnt}")


def main():
    parser = argparse.ArgumentParser(
        prog="agent-guardrail",
        description="Agent Guardrail -- action-level governance for AI agents",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # agents
    p_agents = sub.add_parser("agents", help="List registered agents")
    p_agents.set_defaults(func=cmd_agents)

    # register
    p_register = sub.add_parser("register", help="Register a new agent")
    p_register.add_argument("name", help="Agent name")
    p_register.add_argument("--framework", help="Agent framework (e.g. langchain, crewai)")
    p_register.add_argument("--description", help="Agent description")
    p_register.set_defaults(func=cmd_register)

    # kill / unkill
    p_kill = sub.add_parser("kill", help="Activate kill switch for an agent")
    p_kill.add_argument("agent_id", help="Agent ID")
    p_kill.set_defaults(func=cmd_kill)

    p_unkill = sub.add_parser("unkill", help="Revoke kill switch for an agent")
    p_unkill.add_argument("agent_id", help="Agent ID")
    p_unkill.set_defaults(func=cmd_unkill)

    # policies
    p_policies = sub.add_parser("policies", help="List active policies")
    p_policies.set_defaults(func=cmd_policies)

    # apply-template
    p_template = sub.add_parser("apply-template", help="Apply a policy template")
    p_template.add_argument("template", choices=["restrictive", "moderate", "permissive"])
    p_template.add_argument("agent_id", help="Agent ID (or 'global')")
    p_template.set_defaults(func=cmd_apply_template)

    # actions
    p_actions = sub.add_parser("actions", help="List flight recorder entries")
    p_actions.add_argument("--agent", help="Filter by agent ID")
    p_actions.add_argument("--decision", choices=["allow", "deny", "require_approval"])
    p_actions.add_argument("--limit", type=int, default=30, help="Max entries")
    p_actions.set_defaults(func=cmd_actions)

    # replay
    p_replay = sub.add_parser("replay", help="Replay a session's actions")
    p_replay.add_argument("session_id", help="Session ID")
    p_replay.set_defaults(func=cmd_replay)

    # approvals
    p_approvals = sub.add_parser("approvals", help="List pending approvals")
    p_approvals.add_argument("--agent", help="Filter by agent ID")
    p_approvals.set_defaults(func=cmd_approvals)

    # approve / deny
    p_approve = sub.add_parser("approve", help="Approve a pending action")
    p_approve.add_argument("approval_id", help="Approval ID")
    p_approve.set_defaults(func=cmd_approve)

    p_deny = sub.add_parser("deny", help="Deny a pending action")
    p_deny.add_argument("approval_id", help="Approval ID")
    p_deny.set_defaults(func=cmd_deny_approval)

    # eval
    p_eval = sub.add_parser("eval", help="Test-evaluate an action (dry run)")
    p_eval.add_argument("agent_id", help="Agent ID")
    p_eval.add_argument("action_type", help="Action type (bash, read_file, write_file, etc)")
    p_eval.add_argument("--tool", help="Specific tool name")
    p_eval.add_argument("--target", help="Target path or URL")
    p_eval.add_argument("--cost", type=float, help="Estimated cost USD")
    p_eval.set_defaults(func=cmd_eval)

    # stats
    p_stats = sub.add_parser("stats", help="Show statistics")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
