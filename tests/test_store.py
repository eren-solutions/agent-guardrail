"""Tests for GuardrailStore — agent CRUD, policies, actions, spend, approvals."""

import pytest

from agent_guardrail.store import GuardrailStore


class TestAgentCRUD:
    def test_register_agent(self, store):
        result = store.register_agent(name="my-agent", framework="langchain")
        assert "id" in result
        assert "api_key" in result
        assert result["api_key"].startswith("gw_")

    def test_get_agent(self, store, agent):
        fetched = store.get_agent(agent["id"])
        assert fetched is not None
        assert fetched["name"] == "test-agent"
        assert fetched["framework"] == "pytest"
        assert fetched["enabled"] == 1
        assert fetched["killed"] == 0

    def test_get_agent_not_found(self, store):
        assert store.get_agent("nonexistent") is None

    def test_get_agent_by_key(self, store, agent):
        fetched = store.get_agent_by_key(agent["api_key"])
        assert fetched is not None
        assert fetched["id"] == agent["id"]

    def test_list_agents(self, store):
        store.register_agent(name="agent-a")
        store.register_agent(name="agent-b")
        agents = store.list_agents()
        assert len(agents) == 2
        # Ordered by name
        assert agents[0]["name"] == "agent-a"
        assert agents[1]["name"] == "agent-b"

    def test_update_agent(self, store, agent):
        store.update_agent(agent["id"], name="renamed-agent")
        fetched = store.get_agent(agent["id"])
        assert fetched["name"] == "renamed-agent"

    def test_kill_agent(self, store, agent):
        store.kill_agent(agent["id"])
        fetched = store.get_agent(agent["id"])
        assert fetched["killed"] == 1

    def test_unkill_agent(self, store, agent):
        store.kill_agent(agent["id"])
        store.unkill_agent(agent["id"])
        fetched = store.get_agent(agent["id"])
        assert fetched["killed"] == 0


class TestPolicyCRUD:
    def test_save_and_get_policy(self, store):
        pid = store.save_policy(
            {
                "name": "test-policy",
                "rules": {"tool_denylist": ["sudo"]},
            }
        )
        policies = store.get_policies()
        assert len(policies) == 1
        assert policies[0]["id"] == pid
        assert policies[0]["rules"]["tool_denylist"] == ["sudo"]

    def test_policy_agent_scoping(self, store, agent):
        # Global policy
        store.save_policy({"name": "global-policy", "scope": "global"})
        # Agent-specific policy
        store.save_policy(
            {
                "name": "agent-policy",
                "agent_id": agent["id"],
                "scope": "agent",
            }
        )
        # Get policies for agent — should include global + agent-specific
        policies = store.get_policies(agent_id=agent["id"])
        assert len(policies) == 2

    def test_update_policy(self, store):
        pid = store.save_policy({"name": "old-name"})
        store.update_policy(pid, name="new-name")
        policies = store.get_policies()
        assert policies[0]["name"] == "new-name"

    def test_delete_policy(self, store):
        pid = store.save_policy({"name": "to-delete"})
        store.delete_policy(pid)
        policies = store.get_policies()
        assert len(policies) == 0

    def test_policy_priority_ordering(self, store):
        store.save_policy({"name": "low-priority", "priority": 200})
        store.save_policy({"name": "high-priority", "priority": 10})
        policies = store.get_policies()
        assert policies[0]["name"] == "high-priority"
        assert policies[1]["name"] == "low-priority"


class TestActionRecording:
    def test_record_and_list_actions(self, store, agent):
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "bash",
                "tool_name": "bash",
                "target": "/workspace/test.sh",
                "decision": "allow",
            }
        )
        actions = store.list_actions()
        assert len(actions) == 1
        assert actions[0]["action_type"] == "bash"
        assert actions[0]["decision"] == "allow"

    def test_filter_actions_by_agent(self, store):
        a1 = store.register_agent(name="agent-1")
        a2 = store.register_agent(name="agent-2")
        store.record_action({"agent_id": a1["id"], "action_type": "bash", "decision": "allow"})
        store.record_action({"agent_id": a2["id"], "action_type": "bash", "decision": "deny"})

        actions = store.list_actions(agent_id=a1["id"])
        assert len(actions) == 1
        assert actions[0]["decision"] == "allow"

    def test_filter_actions_by_decision(self, store, agent):
        store.record_action({"agent_id": agent["id"], "action_type": "bash", "decision": "allow"})
        store.record_action({"agent_id": agent["id"], "action_type": "sudo", "decision": "deny"})

        denied = store.list_actions(decision="deny")
        assert len(denied) == 1
        assert denied[0]["action_type"] == "sudo"

    def test_session_replay(self, store, agent):
        for i in range(5):
            store.record_action(
                {
                    "agent_id": agent["id"],
                    "session_id": "session-123",
                    "action_type": "write_file",
                    "decision": "allow",
                }
            )
        replay = store.get_session_replay("session-123")
        assert len(replay) == 5


class TestSpendTracking:
    def test_spend_tracking_on_record(self, store, agent):
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "api_call",
                "decision": "allow",
                "cost_usd": 0.05,
            }
        )
        spend = store.get_spend(agent["id"])
        assert spend["total_usd"] == 0.05
        assert spend["action_count"] == 1

    def test_spend_accumulation(self, store, agent):
        for _ in range(3):
            store.record_action(
                {
                    "agent_id": agent["id"],
                    "action_type": "api_call",
                    "decision": "allow",
                    "cost_usd": 0.10,
                }
            )
        spend = store.get_spend(agent["id"])
        assert abs(spend["total_usd"] - 0.30) < 0.001
        assert spend["action_count"] == 3

    def test_total_spend(self, store, agent):
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "api_call",
                "decision": "allow",
                "cost_usd": 1.50,
            }
        )
        total = store.get_total_spend(agent["id"])
        assert total == 1.50

    def test_denied_count(self, store, agent):
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "bash",
                "decision": "deny",
                "cost_usd": 0.0,
            }
        )
        spend = store.get_spend(agent["id"])
        assert spend["denied_count"] == 1


class TestApprovals:
    def test_create_and_list_approvals(self, store, agent):
        approval_id = store.create_approval(
            action_id="act-1",
            agent_id=agent["id"],
            action_type="bash",
            action_detail={"command": "rm -rf /tmp/test"},
        )
        approvals = store.list_pending_approvals()
        assert len(approvals) == 1
        assert approvals[0]["id"] == approval_id
        assert approvals[0]["status"] == "pending"

    def test_approve_action(self, store, agent):
        approval_id = store.create_approval(
            action_id="act-1",
            agent_id=agent["id"],
            action_type="bash",
            action_detail={},
        )
        store.decide_approval(approval_id, approved=True, decided_by="test")
        approvals = store.list_pending_approvals()
        assert len(approvals) == 0  # No longer pending

    def test_deny_action(self, store, agent):
        approval_id = store.create_approval(
            action_id="act-1",
            agent_id=agent["id"],
            action_type="bash",
            action_detail={},
        )
        store.decide_approval(approval_id, approved=False, decided_by="test")
        approvals = store.list_pending_approvals()
        assert len(approvals) == 0


class TestStats:
    def test_stats_empty(self, store):
        s = store.stats()
        assert s["active_agents"] == 0
        assert s["total_actions"] == 0
        assert s["total_spend_usd"] == 0.0

    def test_stats_with_data(self, store, agent):
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "bash",
                "decision": "allow",
                "cost_usd": 0.25,
            }
        )
        store.record_action(
            {
                "agent_id": agent["id"],
                "action_type": "sudo",
                "decision": "deny",
            }
        )
        s = store.stats()
        assert s["active_agents"] == 1
        assert s["total_actions"] == 2
        assert s["actions_by_decision"]["allow"] == 1
        assert s["actions_by_decision"]["deny"] == 1
