"""
LangChain Guardrail Callback
=============================

Drop-in callback handler that evaluates every tool call through Agent Guardrail
before execution. Blocks denied actions and flags require_approval decisions.

Usage:
    from agent_guardrail.examples.langchain_callback import GuardrailCallback

    callback = GuardrailCallback(agent_id="your-agent-id", db_path="guardrail.db")
    agent = create_react_agent(llm, tools, callbacks=[callback])
"""

from typing import Any, Dict, Optional

from agent_guardrail import GuardrailStore, PolicyEngine


class GuardrailCallback:
    """LangChain BaseCallbackHandler that enforces guardrail policies on tool use.

    Install: pip install langchain-core agent-guardrail

    Example:
        from langchain_core.callbacks import BaseCallbackHandler

        class GuardrailCallback(BaseCallbackHandler):
            ...

    This example shows the pattern. Inherit from BaseCallbackHandler in your
    actual implementation.
    """

    def __init__(
        self,
        agent_id: str,
        db_path: Optional[str] = None,
        session_id: Optional[str] = None,
        raise_on_deny: bool = True,
    ):
        self.agent_id = agent_id
        self.session_id = session_id
        self.raise_on_deny = raise_on_deny
        self._store = GuardrailStore(db_path=db_path)
        self._engine = PolicyEngine(self._store)

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Called before a tool is invoked. Evaluates the action."""
        tool_name = serialized.get("name", "unknown")

        decision = self._engine.evaluate_and_record(
            agent_id=self.agent_id,
            action_type="tool_call",
            tool_name=tool_name,
            target=input_str[:200],  # Truncate for storage
            session_id=self.session_id,
        )

        if decision.decision == "deny":
            msg = f"Guardrail DENIED: {tool_name} -- {decision.reason}"
            if self.raise_on_deny:
                raise PermissionError(msg)
            print(f"WARNING: {msg}")

        elif decision.decision == "require_approval":
            msg = f"Guardrail REQUIRES APPROVAL: {tool_name} -- {decision.reason}"
            if self.raise_on_deny:
                raise PermissionError(msg)
            print(f"WARNING: {msg}")


# -- Quick demo --
if __name__ == "__main__":
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "guardrail.db")
        store = GuardrailStore(db_path=db_path)

        # Register agent and apply moderate policy
        agent = store.register_agent(name="langchain-demo", framework="langchain")
        from agent_guardrail.policy import DEFAULT_POLICIES
        store.save_policy({
            "name": "moderate",
            "agent_id": agent["id"],
            "rules": DEFAULT_POLICIES["moderate"]["rules"],
        })

        # Create callback
        callback = GuardrailCallback(
            agent_id=agent["id"],
            db_path=db_path,
            session_id="demo-session",
        )

        # Simulate tool calls
        print("Simulating LangChain tool calls with guardrail...\n")

        # Safe tool call
        try:
            callback.on_tool_start({"name": "read_file"}, "/workspace/app.py")
            print("  read_file /workspace/app.py  -> ALLOWED")
        except PermissionError as e:
            print(f"  read_file -> BLOCKED: {e}")

        # Dangerous tool call
        try:
            callback.on_tool_start({"name": "sudo"}, "rm -rf /")
            print("  sudo rm -rf /  -> ALLOWED")
        except PermissionError as e:
            print(f"  sudo rm -rf /  -> BLOCKED: {e}")

        # Sensitive target
        try:
            callback.on_tool_start({"name": "read_file"}, "/etc/shadow")
            print("  read_file /etc/shadow  -> ALLOWED")
        except PermissionError as e:
            print(f"  read_file /etc/shadow  -> BLOCKED: {e}")

        print(f"\nFlight recorder: {len(store.list_actions())} actions recorded")
