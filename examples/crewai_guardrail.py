"""
CrewAI Guardrail Integration
=============================

Uses CrewAI's Task.guardrail feature to enforce Agent Guardrail policies.
The guardrail function receives a TaskOutput and returns a GuardrailResult.

Usage with CrewAI:
    from crewai import Task
    task = Task(
        description="...",
        guardrail=make_guardrail("agent-id", db_path="guardrail.db"),
    )

Reference: https://docs.crewai.com/concepts/tasks#task-guardrails
"""

from typing import Optional

from agent_guardrail import GuardrailStore, PolicyEngine


def make_guardrail(
    agent_id: str,
    db_path: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """Create a CrewAI-compatible guardrail function.

    Returns a callable that CrewAI will invoke after each task output.
    The function evaluates the output against guardrail policies.

    Args:
        agent_id: Registered agent ID in the guardrail store.
        db_path: Path to guardrail SQLite database.
        session_id: Optional session ID for flight recorder grouping.

    Returns:
        A guardrail function compatible with CrewAI's Task.guardrail parameter.
    """
    store = GuardrailStore(db_path=db_path)
    engine = PolicyEngine(store)

    def guardrail_check(task_output):
        """Evaluate task output against guardrail policies.

        In a real CrewAI integration, task_output would be a TaskOutput object.
        This function extracts the action details and evaluates them.
        """
        # Extract action info from task output
        output_text = str(task_output) if task_output else ""

        # Determine action type from output content
        action_type = "task_output"
        tool_name = None
        target = output_text[:200] if output_text else None

        decision = engine.evaluate_and_record(
            agent_id=agent_id,
            action_type=action_type,
            tool_name=tool_name,
            target=target,
            session_id=session_id,
        )

        if decision.decision == "deny":
            # Return a tuple (False, reason) to indicate rejection
            return (False, f"Guardrail denied: {decision.reason}")

        if decision.decision == "require_approval":
            return (False, f"Guardrail requires approval: {decision.reason}")

        # Return (True, result) to indicate approval
        return (True, task_output)

    return guardrail_check


# -- Quick demo --
if __name__ == "__main__":
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "guardrail.db")
        store = GuardrailStore(db_path=db_path)

        # Register agent
        agent = store.register_agent(name="crewai-demo", framework="crewai")
        from agent_guardrail.policy import DEFAULT_POLICIES

        store.save_policy(
            {
                "name": "moderate",
                "agent_id": agent["id"],
                "rules": DEFAULT_POLICIES["moderate"]["rules"],
            }
        )

        # Create guardrail
        guardrail = make_guardrail(
            agent_id=agent["id"],
            db_path=db_path,
            session_id="crewai-demo-session",
        )

        print("Simulating CrewAI task guardrail checks...\n")

        # Normal output
        result = guardrail("Generated code review report for /workspace/app.py")
        print(f"  Normal output: {'APPROVED' if result[0] else 'BLOCKED'}")

        # Output referencing sensitive file
        result = guardrail("Read credentials from /etc/shadow")
        print(f"  Sensitive output: {'APPROVED' if result[0] else 'BLOCKED'}")

        print(f"\nFlight recorder: {len(store.list_actions())} actions recorded")
