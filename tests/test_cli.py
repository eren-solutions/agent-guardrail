"""Tests for the CLI — exercises commands via subprocess."""

import json
import os
import subprocess
import sys

import pytest


@pytest.fixture
def cli_env(tmp_path):
    """Set up environment for CLI tests with temp DB."""
    db_path = str(tmp_path / "test_guardrail.db")
    log_dir = str(tmp_path / "logs")
    env = os.environ.copy()
    env["GUARDRAIL_DB"] = db_path
    env["GUARDRAIL_LOG_DIR"] = log_dir
    return env


def run_cli(*args, env=None):
    """Run agent-guardrail CLI and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_guardrail"] + list(args),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


class TestCLIStats:
    def test_stats_empty(self, cli_env):
        rc, out, err = run_cli("stats", env=cli_env)
        assert rc == 0
        assert "Active agents:" in out
        assert "0" in out


class TestCLIRegister:
    def test_register_agent(self, cli_env):
        rc, out, err = run_cli("register", "my-test-agent", env=cli_env)
        assert rc == 0
        assert "Agent registered" in out
        assert "API Key:" in out
        assert "gw_" in out

    def test_register_with_framework(self, cli_env):
        rc, out, err = run_cli("register", "my-agent", "--framework", "langchain", env=cli_env)
        assert rc == 0
        assert "Agent registered" in out


class TestCLIAgents:
    def test_list_agents_empty(self, cli_env):
        rc, out, err = run_cli("agents", env=cli_env)
        assert rc == 0
        assert "No agents registered" in out

    def test_list_agents_with_data(self, cli_env):
        run_cli("register", "agent-one", env=cli_env)
        rc, out, err = run_cli("agents", env=cli_env)
        assert rc == 0
        assert "agent-one" in out
        assert "1 agents" in out


class TestCLIKillSwitch:
    def test_kill_and_unkill(self, cli_env):
        # Register
        rc, out, err = run_cli("register", "killable-agent", env=cli_env)
        assert rc == 0
        # Extract agent ID from output
        for line in out.splitlines():
            if "ID:" in line:
                agent_id = line.strip().split("ID:")[1].strip()
                break

        # Kill
        rc, out, err = run_cli("kill", agent_id, env=cli_env)
        assert rc == 0
        assert "ACTIVATED" in out

        # Unkill
        rc, out, err = run_cli("unkill", agent_id, env=cli_env)
        assert rc == 0
        assert "REVOKED" in out


class TestCLIPolicies:
    def test_list_policies_empty(self, cli_env):
        rc, out, err = run_cli("policies", env=cli_env)
        assert rc == 0
        assert "No policies configured" in out

    def test_apply_template(self, cli_env):
        # Register agent first
        rc, out, err = run_cli("register", "policy-agent", env=cli_env)
        for line in out.splitlines():
            if "ID:" in line:
                agent_id = line.strip().split("ID:")[1].strip()
                break

        # Apply moderate template
        rc, out, err = run_cli("apply-template", "moderate", agent_id, env=cli_env)
        assert rc == 0
        assert "Applied 'moderate' template" in out

        # Verify it shows up
        rc, out, err = run_cli("policies", env=cli_env)
        assert "Moderate" in out


class TestCLIEval:
    def test_eval_no_policy(self, cli_env):
        # Register agent
        rc, out, err = run_cli("register", "eval-agent", env=cli_env)
        for line in out.splitlines():
            if "ID:" in line:
                agent_id = line.strip().split("ID:")[1].strip()
                break

        # Eval should allow (no policies)
        rc, out, err = run_cli("eval", agent_id, "bash", env=cli_env)
        assert rc == 0
        assert "allow" in out

    def test_eval_with_target(self, cli_env):
        rc, out, err = run_cli("register", "eval-agent2", env=cli_env)
        for line in out.splitlines():
            if "ID:" in line:
                agent_id = line.strip().split("ID:")[1].strip()
                break

        # Apply restrictive template
        run_cli("apply-template", "restrictive", agent_id, env=cli_env)

        # Eval target /etc/shadow — should deny
        rc, out, err = run_cli(
            "eval", agent_id, "read_file", "--target", "/etc/shadow", env=cli_env
        )
        assert rc == 0
        assert "deny" in out


class TestCLIHelp:
    def test_help(self, cli_env):
        rc, out, err = run_cli("--help", env=cli_env)
        assert rc == 0
        assert "agent-guardrail" in out

    def test_no_args_shows_help(self, cli_env):
        rc, out, err = run_cli(env=cli_env)
        assert rc == 0
        assert "agent-guardrail" in out
