"""
Agent Guardrail
===============

Action-level governance for AI agents — control what they DO, not what they SAY.

Architecture:
  Agent Framework -> Guardrail API -> {allow, deny, require_approval}
                                    -> Flight Recorder logs all actions

Components:
  - Policy engine: Allowlists, deny patterns, spend/time caps, kill switch
  - Flight recorder: Full action history with replay capability
  - Store: SQLite persistence for policies, actions, agents
"""

from .policy import PolicyEngine, PolicyDecision, DEFAULT_POLICIES
from .store import GuardrailStore

__version__ = "0.1.1"
__all__ = [
    "PolicyEngine",
    "PolicyDecision",
    "GuardrailStore",
    "DEFAULT_POLICIES",
    "__version__",
]
