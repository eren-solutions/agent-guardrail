"""
Guardrail Decorator
===================

A simple @guardrail decorator that wraps any function with policy evaluation.
Works with any Python code -- no framework dependency.

Usage:
    from agent_guardrail.examples.decorator import guardrail

    @guardrail("my-agent-id", action_type="bash")
    def run_command(cmd: str) -> str:
        return subprocess.run(cmd, shell=True, capture_output=True).stdout
"""

import functools
from typing import Callable, Optional

from agent_guardrail import GuardrailStore, PolicyEngine


def guardrail(
    agent_id: str,
    action_type: str = "function_call",
    db_path: Optional[str] = None,
    session_id: Optional[str] = None,
    raise_on_deny: bool = True,
) -> Callable:
    """Decorator that enforces guardrail policies on function calls.

    Args:
        agent_id: Registered agent ID.
        action_type: Category for policy evaluation.
        db_path: Path to guardrail database.
        session_id: Optional session ID for flight recorder.
        raise_on_deny: If True, raises PermissionError on deny. Otherwise returns None.

    Returns:
        Decorator function.
    """
    store = GuardrailStore(db_path=db_path)
    engine = PolicyEngine(store)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Use first positional arg or 'target' kwarg as target
            target = None
            if args:
                target = str(args[0])[:200]
            elif "target" in kwargs:
                target = str(kwargs["target"])[:200]

            decision = engine.evaluate_and_record(
                agent_id=agent_id,
                action_type=action_type,
                tool_name=func.__name__,
                target=target,
                session_id=session_id,
            )

            if decision.decision == "deny":
                msg = f"Guardrail DENIED {func.__name__}: {decision.reason}"
                if raise_on_deny:
                    raise PermissionError(msg)
                print(f"WARNING: {msg}")
                return None

            if decision.decision == "require_approval":
                msg = f"Guardrail REQUIRES APPROVAL for {func.__name__}: {decision.reason}"
                if raise_on_deny:
                    raise PermissionError(msg)
                print(f"WARNING: {msg}")
                return None

            return func(*args, **kwargs)

        return wrapper
    return decorator


# -- Quick demo --
if __name__ == "__main__":
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "guardrail.db")
        store = GuardrailStore(db_path=db_path)

        # Register agent
        agent = store.register_agent(name="decorator-demo")
        from agent_guardrail.policy import DEFAULT_POLICIES
        store.save_policy({
            "name": "moderate",
            "agent_id": agent["id"],
            "rules": DEFAULT_POLICIES["moderate"]["rules"],
        })

        # Define guarded functions
        @guardrail(agent["id"], action_type="read_file", db_path=db_path)
        def read_file(path: str) -> str:
            return f"Contents of {path}"

        @guardrail(agent["id"], action_type="bash", db_path=db_path)
        def run_command(cmd: str) -> str:
            return f"Executed: {cmd}"

        print("Simulating decorated function calls...\n")

        # Safe read
        try:
            result = read_file("/workspace/app.py")
            print(f"  read_file(/workspace/app.py) -> {result}")
        except PermissionError as e:
            print(f"  read_file(/workspace/app.py) -> BLOCKED: {e}")

        # Dangerous read
        try:
            result = read_file("/etc/shadow")
            print(f"  read_file(/etc/shadow) -> {result}")
        except PermissionError as e:
            print(f"  read_file(/etc/shadow) -> BLOCKED: {e}")

        # Sudo command
        try:
            result = run_command("sudo rm -rf /")
            print(f"  run_command(sudo ...) -> {result}")
        except PermissionError as e:
            print(f"  run_command(sudo ...) -> BLOCKED: {e}")

        print(f"\nFlight recorder: {len(store.list_actions())} actions recorded")
