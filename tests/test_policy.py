"""Tests for PolicyEngine — decision flow, templates, spend caps, risk thresholds."""

import pytest

from agent_guardrail.policy import PolicyEngine, PolicyDecision, DEFAULT_POLICIES


class TestBasicDecisions:
    def test_allow_when_no_store(self):
        engine = PolicyEngine(store=None)
        decision = engine.evaluate("any-agent", "bash")
        assert decision.decision == "allow"

    def test_deny_unregistered_agent(self, store):
        engine = PolicyEngine(store)
        decision = engine.evaluate("nonexistent", "bash")
        assert decision.decision == "deny"
        assert "not registered" in decision.reason

    def test_allow_registered_agent_no_policies(self, store, agent):
        engine = PolicyEngine(store)
        decision = engine.evaluate(agent["id"], "bash")
        assert decision.decision == "allow"


class TestKillSwitch:
    def test_kill_switch_denies_all(self, store, agent, engine):
        store.kill_agent(agent["id"])
        decision = engine.evaluate(agent["id"], "read_file")
        assert decision.decision == "deny"
        assert "Kill switch" in decision.reason

    def test_unkill_allows_again(self, store, agent, engine):
        store.kill_agent(agent["id"])
        store.unkill_agent(agent["id"])
        decision = engine.evaluate(agent["id"], "read_file")
        assert decision.decision == "allow"


class TestToolDenylist:
    def test_deny_sudo(self, store, agent, engine):
        store.save_policy(
            {
                "name": "no-sudo",
                "agent_id": agent["id"],
                "rules": {"tool_denylist": ["sudo"]},
            }
        )
        decision = engine.evaluate(agent["id"], "sudo")
        assert decision.decision == "deny"
        assert "denied by policy" in decision.reason

    def test_deny_with_glob_pattern(self, store, agent, engine):
        store.save_policy(
            {
                "name": "no-delete",
                "agent_id": agent["id"],
                "rules": {"tool_denylist": ["delete*"]},
            }
        )
        decision = engine.evaluate(agent["id"], "delete_file")
        assert decision.decision == "deny"


class TestTargetDenylist:
    def test_deny_etc_path(self, store, agent, engine):
        store.save_policy(
            {
                "name": "no-etc",
                "agent_id": agent["id"],
                "rules": {"target_denylist": ["/etc/*"]},
            }
        )
        decision = engine.evaluate(agent["id"], "read_file", target="/etc/shadow")
        assert decision.decision == "deny"

    def test_deny_env_files(self, store, agent, engine):
        store.save_policy(
            {
                "name": "no-env",
                "agent_id": agent["id"],
                "rules": {"target_denylist": ["*.env"]},
            }
        )
        decision = engine.evaluate(agent["id"], "read_file", target="/app/.env")
        assert decision.decision == "deny"

    def test_allow_safe_target(self, store, agent, engine):
        store.save_policy(
            {
                "name": "no-etc",
                "agent_id": agent["id"],
                "rules": {"target_denylist": ["/etc/*"]},
            }
        )
        decision = engine.evaluate(agent["id"], "read_file", target="/workspace/app.py")
        assert decision.decision == "allow"


class TestToolAllowlist:
    def test_allowlist_blocks_unlisted_tools(self, store, agent, engine):
        store.save_policy(
            {
                "name": "read-only",
                "agent_id": agent["id"],
                "rules": {"tool_allowlist": ["read_file", "list_files"]},
            }
        )
        decision = engine.evaluate(agent["id"], "write_file")
        assert decision.decision == "deny"
        assert "not in allowlist" in decision.reason

    def test_allowlist_allows_listed_tools(self, store, agent, engine):
        store.save_policy(
            {
                "name": "read-only",
                "agent_id": agent["id"],
                "rules": {"tool_allowlist": ["read_file", "list_files"]},
            }
        )
        decision = engine.evaluate(agent["id"], "read_file")
        assert decision.decision == "allow"


class TestSpendCaps:
    def test_daily_spend_cap(self, store, agent, engine):
        store.save_policy(
            {
                "name": "budget",
                "agent_id": agent["id"],
                "rules": {"spend_cap_daily_usd": 1.0},
            }
        )
        # Record spend near the cap
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "api_call",
                "decision": "allow",
                "cost_usd": 0.90,
            }
        )
        # This should exceed the cap
        decision = engine.evaluate(agent["id"], "api_call", cost_usd=0.20)
        assert decision.decision == "deny"
        assert "Daily spend cap" in decision.reason

    def test_under_daily_cap_allows(self, store, agent, engine):
        store.save_policy(
            {
                "name": "budget",
                "agent_id": agent["id"],
                "rules": {"spend_cap_daily_usd": 10.0},
            }
        )
        decision = engine.evaluate(agent["id"], "api_call", cost_usd=0.50)
        assert decision.decision == "allow"

    def test_total_spend_cap(self, store, agent, engine):
        store.save_policy(
            {
                "name": "budget",
                "agent_id": agent["id"],
                "rules": {"spend_cap_total_usd": 5.0},
            }
        )
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "api_call",
                "decision": "allow",
                "cost_usd": 4.50,
            }
        )
        decision = engine.evaluate(agent["id"], "api_call", cost_usd=1.00)
        assert decision.decision == "deny"
        assert "Total spend cap" in decision.reason


class TestApprovalRequirements:
    def test_require_approval_for_bash(self, store, agent, engine):
        store.save_policy(
            {
                "name": "approve-bash",
                "agent_id": agent["id"],
                "rules": {"require_approval": ["bash"]},
            }
        )
        decision = engine.evaluate(agent["id"], "bash")
        assert decision.decision == "require_approval"

    def test_require_approval_glob(self, store, agent, engine):
        store.save_policy(
            {
                "name": "approve-deletes",
                "agent_id": agent["id"],
                "rules": {"require_approval": ["delete*"]},
            }
        )
        decision = engine.evaluate(agent["id"], "delete_file")
        assert decision.decision == "require_approval"


class TestRiskThreshold:
    def test_high_risk_triggers_approval(self, store, agent, engine):
        store.save_policy(
            {
                "name": "risk-gate",
                "agent_id": agent["id"],
                "rules": {"risk_threshold": 0.5},
            }
        )
        # "bash" has risk 0.7
        decision = engine.evaluate(agent["id"], "bash")
        assert decision.decision == "require_approval"
        assert "Risk score" in decision.reason

    def test_low_risk_passes(self, store, agent, engine):
        store.save_policy(
            {
                "name": "risk-gate",
                "agent_id": agent["id"],
                "rules": {"risk_threshold": 0.5},
            }
        )
        # "read_file" has risk 0.1
        decision = engine.evaluate(agent["id"], "read_file")
        assert decision.decision == "allow"


class TestNetworkDenylist:
    def test_deny_network_target(self, store, agent, engine):
        store.save_policy(
            {
                "name": "no-network",
                "agent_id": agent["id"],
                "rules": {"network_denylist": ["*"]},
            }
        )
        decision = engine.evaluate(agent["id"], "network_request", target="evil.com")
        assert decision.decision == "deny"

    def test_network_allowlist_override(self, store, agent, engine):
        store.save_policy(
            {
                "name": "restricted-network",
                "agent_id": agent["id"],
                "rules": {
                    "network_denylist": ["*"],
                    "network_allowlist": ["api.openai.com"],
                },
            }
        )
        decision = engine.evaluate(agent["id"], "network_request", target="api.openai.com")
        assert decision.decision == "allow"


class TestEvaluateAndRecord:
    def test_evaluate_and_record_creates_action(self, store, agent, engine):
        decision = engine.evaluate_and_record(
            agent_id=agent["id"],
            action_type="read_file",
            target="/workspace/app.py",
            session_id="sess-1",
        )
        assert decision.decision == "allow"
        actions = store.list_actions(agent_id=agent["id"])
        assert len(actions) == 1
        assert actions[0]["target"] == "/workspace/app.py"

    def test_evaluate_and_record_creates_approval(self, store, agent, engine):
        store.save_policy(
            {
                "name": "approve-bash",
                "agent_id": agent["id"],
                "rules": {"require_approval": ["bash"]},
            }
        )
        decision = engine.evaluate_and_record(
            agent_id=agent["id"],
            action_type="bash",
            tool_name="bash",
        )
        assert decision.decision == "require_approval"
        assert "approval_id" in decision.metadata
        approvals = store.list_pending_approvals()
        assert len(approvals) == 1


class TestDefaultTemplates:
    def test_restrictive_template_exists(self):
        assert "restrictive" in DEFAULT_POLICIES
        rules = DEFAULT_POLICIES["restrictive"]["rules"]
        assert "tool_allowlist" in rules
        assert "sudo" in rules["tool_denylist"]

    def test_moderate_template_exists(self):
        assert "moderate" in DEFAULT_POLICIES
        rules = DEFAULT_POLICIES["moderate"]["rules"]
        assert rules["spend_cap_daily_usd"] == 25.0

    def test_permissive_template_exists(self):
        assert "permissive" in DEFAULT_POLICIES
        rules = DEFAULT_POLICIES["permissive"]["rules"]
        assert rules["spend_cap_daily_usd"] == 50.0

    def test_restrictive_denies_bash(self, store, agent, engine):
        template = DEFAULT_POLICIES["restrictive"]
        store.save_policy(
            {
                "name": template["name"],
                "agent_id": agent["id"],
                "rules": template["rules"],
            }
        )
        decision = engine.evaluate(agent["id"], "bash")
        assert decision.decision == "require_approval"

    def test_moderate_denies_sudo(self, store, agent, engine):
        template = DEFAULT_POLICIES["moderate"]
        store.save_policy(
            {
                "name": template["name"],
                "agent_id": agent["id"],
                "rules": template["rules"],
            }
        )
        decision = engine.evaluate(agent["id"], "sudo")
        assert decision.decision == "deny"

    def test_permissive_allows_most(self, store, agent, engine):
        template = DEFAULT_POLICIES["permissive"]
        store.save_policy(
            {
                "name": template["name"],
                "agent_id": agent["id"],
                "rules": template["rules"],
            }
        )
        decision = engine.evaluate(agent["id"], "bash")
        assert decision.decision == "allow"
