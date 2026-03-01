"""Shared test fixtures for agent-guardrail tests."""

import os
import pytest

from agent_guardrail.store import GuardrailStore
from agent_guardrail.policy import PolicyEngine, DEFAULT_POLICIES


@pytest.fixture
def store(tmp_path):
    """Create a GuardrailStore with a temporary database."""
    db_path = str(tmp_path / "test_guardrail.db")
    return GuardrailStore(db_path=db_path)


@pytest.fixture
def engine(store):
    """Create a PolicyEngine backed by the test store."""
    return PolicyEngine(store)


@pytest.fixture
def agent(store):
    """Register a test agent and return its details."""
    result = store.register_agent(
        name="test-agent",
        framework="pytest",
        description="Test agent for unit tests",
    )
    return result


@pytest.fixture
def agent_with_moderate_policy(store, agent):
    """Register a test agent with the moderate policy template applied."""
    template = DEFAULT_POLICIES["moderate"]
    store.save_policy(
        {
            "name": template["name"],
            "description": template.get("description", ""),
            "agent_id": agent["id"],
            "scope": "agent",
            "priority": 50,
            "rules": template["rules"],
        }
    )
    return agent
