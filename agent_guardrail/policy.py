"""
Policy Engine
=============

Evaluates agent actions against configured policies.

Policy rule structure:
{
    "tool_allowlist": ["read_file", "write_file", "bash"],
    "tool_denylist": ["delete_file", "sudo"],
    "target_allowlist": ["/workspace/*"],
    "target_denylist": ["/etc/*", "/root/*", "*.env"],
    "network_allowlist": ["api.openai.com", "*.anthropic.com"],
    "network_denylist": ["*"],
    "spend_cap_daily_usd": 10.0,
    "spend_cap_total_usd": 100.0,
    "time_cap_seconds": 3600,
    "action_rate_limit": 100,
    "require_approval": ["bash", "network_request", "delete_*"],
    "risk_threshold": 0.8
}

Decision flow:
1. Kill switch check -> deny if killed
2. Agent enabled check -> deny if disabled
3. Spend cap check -> deny if exceeded
4. Tool denylist check -> deny if matched
5. Target denylist check -> deny if matched
6. Approval requirement check -> require_approval if matched
7. Tool allowlist check -> deny if not in list (when list is non-empty)
8. Default -> allow
"""

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Result of evaluating an action against policies."""
    decision: str  # "allow", "deny", "require_approval"
    reason: str = ""
    policy_id: Optional[str] = None
    risk_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


# Risk scores for action types
ACTION_RISK = {
    "bash": 0.7,
    "exec": 0.8,
    "delete": 0.9,
    "network_request": 0.5,
    "write_file": 0.3,
    "read_file": 0.1,
    "api_call": 0.4,
    "install": 0.6,
    "sudo": 1.0,
    "spawn_agent": 0.6,
}


def _matches_pattern(value: str, patterns: List[str]) -> bool:
    """Check if value matches any glob/fnmatch pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(value, pattern):
            return True
        if fnmatch.fnmatch(value.lower(), pattern.lower()):
            return True
    return False


class PolicyEngine:
    """Evaluates actions against a set of policies."""

    def __init__(self, store=None):
        self._store = store

    def evaluate(
        self,
        agent_id: str,
        action_type: str,
        tool_name: Optional[str] = None,
        target: Optional[str] = None,
        cost_usd: float = 0.0,
        detail: Optional[Dict] = None,
    ) -> PolicyDecision:
        """Evaluate an action against all applicable policies.

        Args:
            agent_id: The agent attempting the action.
            action_type: Category (bash, read_file, write_file, network_request, etc).
            tool_name: Specific tool being invoked.
            target: File path, URL, or other target.
            cost_usd: Estimated cost of this action.
            detail: Additional context dict.

        Returns:
            PolicyDecision with allow/deny/require_approval.
        """
        if not self._store:
            return PolicyDecision(decision="allow", reason="No store configured")

        # 1. Check kill switch
        agent = self._store.get_agent(agent_id)
        if not agent:
            return PolicyDecision(decision="deny", reason="Agent not registered")
        if agent.get("killed"):
            return PolicyDecision(decision="deny", reason="Kill switch active",
                                  risk_score=1.0)
        if not agent.get("enabled"):
            return PolicyDecision(decision="deny", reason="Agent disabled")

        # 2. Get applicable policies (global + agent-specific, ordered by priority)
        policies = self._store.get_policies(agent_id=agent_id)

        # Base risk score
        risk_score = ACTION_RISK.get(action_type, 0.3)
        effective_tool = tool_name or action_type

        # 3. Evaluate each policy
        for policy in policies:
            rules = policy.get("rules", {})
            pid = policy.get("id")

            # Spend cap check
            daily_cap = rules.get("spend_cap_daily_usd")
            if daily_cap is not None:
                spend = self._store.get_spend(agent_id)
                if spend["total_usd"] + cost_usd > daily_cap:
                    return PolicyDecision(
                        decision="deny",
                        reason=f"Daily spend cap exceeded (${spend['total_usd']:.2f} / ${daily_cap:.2f})",
                        policy_id=pid, risk_score=risk_score,
                    )

            total_cap = rules.get("spend_cap_total_usd")
            if total_cap is not None:
                total = self._store.get_total_spend(agent_id)
                if total + cost_usd > total_cap:
                    return PolicyDecision(
                        decision="deny",
                        reason=f"Total spend cap exceeded (${total:.2f} / ${total_cap:.2f})",
                        policy_id=pid, risk_score=risk_score,
                    )

            # Tool denylist
            denylist = rules.get("tool_denylist", [])
            if denylist and _matches_pattern(effective_tool, denylist):
                return PolicyDecision(
                    decision="deny",
                    reason=f"Tool '{effective_tool}' is denied by policy '{policy.get('name')}'",
                    policy_id=pid, risk_score=risk_score,
                )

            # Target denylist
            target_deny = rules.get("target_denylist", [])
            if target and target_deny and _matches_pattern(target, target_deny):
                return PolicyDecision(
                    decision="deny",
                    reason=f"Target '{target}' is denied by policy '{policy.get('name')}'",
                    policy_id=pid, risk_score=risk_score,
                )

            # Network denylist
            network_deny = rules.get("network_denylist", [])
            if action_type == "network_request" and target and network_deny:
                if _matches_pattern(target, network_deny):
                    # Check allowlist override
                    network_allow = rules.get("network_allowlist", [])
                    if not (network_allow and _matches_pattern(target, network_allow)):
                        return PolicyDecision(
                            decision="deny",
                            reason=f"Network target '{target}' denied",
                            policy_id=pid, risk_score=risk_score,
                        )

            # Approval requirements
            approval_patterns = rules.get("require_approval", [])
            if approval_patterns and _matches_pattern(effective_tool, approval_patterns):
                return PolicyDecision(
                    decision="require_approval",
                    reason=f"Tool '{effective_tool}' requires human approval",
                    policy_id=pid, risk_score=risk_score,
                )

            # Risk threshold
            risk_threshold = rules.get("risk_threshold")
            if risk_threshold is not None and risk_score >= risk_threshold:
                return PolicyDecision(
                    decision="require_approval",
                    reason=f"Risk score {risk_score:.2f} exceeds threshold {risk_threshold}",
                    policy_id=pid, risk_score=risk_score,
                )

            # Tool allowlist (if specified, only listed tools are allowed)
            allowlist = rules.get("tool_allowlist", [])
            if allowlist and not _matches_pattern(effective_tool, allowlist):
                return PolicyDecision(
                    decision="deny",
                    reason=f"Tool '{effective_tool}' not in allowlist",
                    policy_id=pid, risk_score=risk_score,
                )

            # Target allowlist
            target_allow = rules.get("target_allowlist", [])
            if target and target_allow and not _matches_pattern(target, target_allow):
                return PolicyDecision(
                    decision="deny",
                    reason=f"Target '{target}' not in allowlist",
                    policy_id=pid, risk_score=risk_score,
                )

        # Default: allow
        return PolicyDecision(decision="allow", reason="No policy violated",
                              risk_score=risk_score)

    def evaluate_and_record(
        self,
        agent_id: str,
        action_type: str,
        tool_name: Optional[str] = None,
        target: Optional[str] = None,
        cost_usd: float = 0.0,
        session_id: Optional[str] = None,
        detail: Optional[Dict] = None,
    ) -> PolicyDecision:
        """Evaluate an action and record it to the flight recorder."""
        decision = self.evaluate(
            agent_id=agent_id,
            action_type=action_type,
            tool_name=tool_name,
            target=target,
            cost_usd=cost_usd,
            detail=detail,
        )

        if self._store:
            self._store.record_action({
                "agent_id": agent_id,
                "session_id": session_id,
                "action_type": action_type,
                "action_detail": detail or {},
                "tool_name": tool_name,
                "target": target,
                "decision": decision.decision,
                "decision_reason": decision.reason,
                "policy_id": decision.policy_id,
                "cost_usd": cost_usd,
                "risk_score": decision.risk_score,
            })

            # Create approval request if needed
            if decision.decision == "require_approval":
                approval_id = self._store.create_approval(
                    action_id="pending",
                    agent_id=agent_id,
                    action_type=action_type,
                    action_detail=detail or {"tool": tool_name, "target": target},
                )
                decision.metadata["approval_id"] = approval_id

        return decision


# -- Default policy templates ----------------------------------------

DEFAULT_POLICIES = {
    "restrictive": {
        "name": "Restrictive -- Read-only with approval gates",
        "description": "Only allows read operations. Writes and execution require approval.",
        "rules": {
            "tool_allowlist": ["read_file", "list_files", "search", "api_call"],
            "tool_denylist": ["sudo", "rm", "delete*"],
            "target_denylist": ["/etc/*", "/root/*", "*.env", "*.key", "*.pem"],
            "require_approval": ["bash", "write_file", "network_request", "exec"],
            "spend_cap_daily_usd": 5.0,
            "risk_threshold": 0.6,
        },
    },
    "moderate": {
        "name": "Moderate -- Standard development with guardrails",
        "description": "Allows most dev operations. Blocks dangerous commands and sensitive paths.",
        "rules": {
            "tool_denylist": ["sudo", "rm -rf", "curl|bash", "eval"],
            "target_denylist": ["/etc/*", "/root/*", "*.env", "*.key", "*.pem",
                                "*credentials*", "*secret*"],
            "require_approval": ["delete*", "install", "spawn_agent"],
            "spend_cap_daily_usd": 25.0,
            "spend_cap_total_usd": 500.0,
            "risk_threshold": 0.8,
        },
    },
    "permissive": {
        "name": "Permissive -- Full access with spend caps",
        "description": "Allows all operations. Only enforces spend caps and blocks truly dangerous targets.",
        "rules": {
            "tool_denylist": ["sudo"],
            "target_denylist": ["*.env", "*.key", "*.pem"],
            "spend_cap_daily_usd": 50.0,
            "spend_cap_total_usd": 1000.0,
        },
    },
}
