"""
Guardrail Store
===============

SQLite persistence for policies, agent registrations, actions, and spend tracking.

Database: ~/.agent-guardrail/guardrail.db (configurable via GUARDRAIL_DB env var)
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.expanduser(
    os.environ.get("GUARDRAIL_DB", "~/.agent-guardrail/guardrail.db")
)


class GuardrailStore:
    """SQLite storage for Agent Guardrail Gateway."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self) -> None:
        if self._initialized:
            return

        conn = self._db()
        try:
            cur = conn.cursor()

            # Registered agents
            cur.execute("""
                CREATE TABLE IF NOT EXISTS guardrail_agents (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    framework   TEXT,
                    description TEXT,
                    api_key     TEXT UNIQUE,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    killed      INTEGER NOT NULL DEFAULT 0,
                    metadata    TEXT DEFAULT '{}',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)

            # Policy definitions
            cur.execute("""
                CREATE TABLE IF NOT EXISTS guardrail_policies (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT,
                    agent_id    TEXT REFERENCES guardrail_agents(id),
                    scope       TEXT NOT NULL DEFAULT 'global',
                    priority    INTEGER NOT NULL DEFAULT 100,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    rules       TEXT NOT NULL DEFAULT '{}',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)

            # Action log (flight recorder)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS guardrail_actions (
                    id              TEXT PRIMARY KEY,
                    agent_id        TEXT NOT NULL,
                    session_id      TEXT,
                    action_type     TEXT NOT NULL,
                    action_detail   TEXT NOT NULL DEFAULT '{}',
                    tool_name       TEXT,
                    target          TEXT,
                    decision        TEXT NOT NULL,
                    decision_reason TEXT,
                    policy_id       TEXT,
                    cost_usd        REAL DEFAULT 0.0,
                    duration_ms     REAL,
                    risk_score      REAL DEFAULT 0.0,
                    metadata        TEXT DEFAULT '{}',
                    created_at      TEXT NOT NULL
                )
            """)

            # Spend tracking per agent
            cur.execute("""
                CREATE TABLE IF NOT EXISTS guardrail_spend (
                    id          TEXT PRIMARY KEY,
                    agent_id    TEXT NOT NULL REFERENCES guardrail_agents(id),
                    period      TEXT NOT NULL,
                    total_usd   REAL NOT NULL DEFAULT 0.0,
                    action_count INTEGER NOT NULL DEFAULT 0,
                    denied_count INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT NOT NULL,
                    UNIQUE(agent_id, period)
                )
            """)

            # Approval queue
            cur.execute("""
                CREATE TABLE IF NOT EXISTS guardrail_approvals (
                    id          TEXT PRIMARY KEY,
                    action_id   TEXT NOT NULL,
                    agent_id    TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_detail TEXT NOT NULL DEFAULT '{}',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    decided_by  TEXT,
                    decided_at  TEXT,
                    expires_at  TEXT,
                    created_at  TEXT NOT NULL
                )
            """)

            # Indices
            cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_agent ON guardrail_actions(agent_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_created ON guardrail_actions(created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_decision ON guardrail_actions(decision)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_session ON guardrail_actions(session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_type ON guardrail_actions(action_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_policies_agent ON guardrail_policies(agent_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_policies_scope ON guardrail_policies(scope)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_spend_agent ON guardrail_spend(agent_id, period)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_approvals_status ON guardrail_approvals(status)")

            conn.commit()
            self._initialized = True
            logger.info("Guardrail tables initialized at %s", self.db_path)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    def register_agent(
        self, name: str, framework: str = "", description: str = "",
        metadata: Optional[Dict] = None,
    ) -> Dict[str, str]:
        """Register an agent. Returns {id, api_key}."""
        self._ensure_tables()
        conn = self._db()
        try:
            agent_id = str(uuid.uuid4())
            api_key = f"gw_{uuid.uuid4().hex}"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO guardrail_agents "
                "(id, name, framework, description, api_key, metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (agent_id, name, framework, description, api_key,
                 json.dumps(metadata or {}), now, now),
            )
            conn.commit()
            return {"id": agent_id, "api_key": api_key}
        finally:
            conn.close()

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM guardrail_agents WHERE id = ?", (agent_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_agent_by_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM guardrail_agents WHERE api_key = ?", (api_key,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_agents(self) -> List[Dict[str, Any]]:
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM guardrail_agents ORDER BY name")
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def update_agent(self, agent_id: str, **fields) -> bool:
        self._ensure_tables()
        if not fields:
            return False
        conn = self._db()
        try:
            fields["updated_at"] = datetime.now(timezone.utc).isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [agent_id]
            conn.execute(f"UPDATE guardrail_agents SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return True
        finally:
            conn.close()

    def kill_agent(self, agent_id: str) -> bool:
        """Emergency kill switch -- immediately deny all actions."""
        return self.update_agent(agent_id, killed=1)

    def unkill_agent(self, agent_id: str) -> bool:
        return self.update_agent(agent_id, killed=0)

    # ------------------------------------------------------------------
    # Policies
    # ------------------------------------------------------------------

    def save_policy(self, policy: Dict[str, Any]) -> str:
        self._ensure_tables()
        conn = self._db()
        try:
            policy_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO guardrail_policies "
                "(id, name, description, agent_id, scope, priority, rules, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    policy_id,
                    policy.get("name", ""),
                    policy.get("description", ""),
                    policy.get("agent_id"),
                    policy.get("scope", "global"),
                    policy.get("priority", 100),
                    json.dumps(policy.get("rules", {})),
                    now, now,
                ),
            )
            conn.commit()
            return policy_id
        finally:
            conn.close()

    def get_policies(
        self, agent_id: Optional[str] = None, scope: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get applicable policies, ordered by priority."""
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            query = "SELECT * FROM guardrail_policies WHERE enabled = 1"
            params: list = []

            if agent_id:
                query += " AND (agent_id = ? OR agent_id IS NULL)"
                params.append(agent_id)
            if scope:
                query += " AND scope = ?"
                params.append(scope)

            query += " ORDER BY priority ASC"
            cur.execute(query, params)
            return [
                {**dict(row), "rules": json.loads(row["rules"] or "{}")}
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def update_policy(self, policy_id: str, **fields) -> bool:
        self._ensure_tables()
        if not fields:
            return False
        conn = self._db()
        try:
            fields["updated_at"] = datetime.now(timezone.utc).isoformat()
            if "rules" in fields and not isinstance(fields["rules"], str):
                fields["rules"] = json.dumps(fields["rules"])
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [policy_id]
            conn.execute(f"UPDATE guardrail_policies SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return True
        finally:
            conn.close()

    def delete_policy(self, policy_id: str) -> bool:
        self._ensure_tables()
        conn = self._db()
        try:
            conn.execute("DELETE FROM guardrail_policies WHERE id = ?", (policy_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Actions (flight recorder)
    # ------------------------------------------------------------------

    def record_action(self, action: Dict[str, Any]) -> str:
        """Record an evaluated action to the flight recorder."""
        self._ensure_tables()
        conn = self._db()
        try:
            action_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO guardrail_actions "
                "(id, agent_id, session_id, action_type, action_detail, tool_name, "
                "target, decision, decision_reason, policy_id, cost_usd, "
                "duration_ms, risk_score, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    action_id,
                    action.get("agent_id", ""),
                    action.get("session_id"),
                    action.get("action_type", "unknown"),
                    json.dumps(action.get("action_detail", {})),
                    action.get("tool_name"),
                    action.get("target"),
                    action.get("decision", "allow"),
                    action.get("decision_reason"),
                    action.get("policy_id"),
                    action.get("cost_usd", 0.0),
                    action.get("duration_ms"),
                    action.get("risk_score", 0.0),
                    json.dumps(action.get("metadata", {})),
                    now,
                ),
            )

            # Update spend tracking
            period = now[:10]  # YYYY-MM-DD
            agent_id = action.get("agent_id", "")
            cost = action.get("cost_usd", 0.0)
            decision = action.get("decision", "allow")

            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM guardrail_spend WHERE agent_id = ? AND period = ?",
                (agent_id, period),
            )
            existing = cur.fetchone()
            if existing:
                denied_inc = 1 if decision == "deny" else 0
                conn.execute(
                    "UPDATE guardrail_spend SET total_usd = total_usd + ?, "
                    "action_count = action_count + 1, "
                    "denied_count = denied_count + ?, updated_at = ? "
                    "WHERE id = ?",
                    (cost, denied_inc, now, existing["id"]),
                )
            else:
                spend_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO guardrail_spend "
                    "(id, agent_id, period, total_usd, action_count, denied_count, updated_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (spend_id, agent_id, period, cost,
                     1 if decision == "deny" else 0, now),
                )

            conn.commit()
            return action_id
        finally:
            conn.close()

    def list_actions(
        self,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        decision: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            query = "SELECT * FROM guardrail_actions WHERE 1=1"
            params: list = []

            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)
            if decision:
                query += " AND decision = ?"
                params.append(decision)

            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cur.execute(query, params)
            return [
                {
                    **dict(row),
                    "action_detail": json.loads(row["action_detail"] or "{}"),
                    "metadata": json.loads(row["metadata"] or "{}"),
                }
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def get_session_replay(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all actions for a session in chronological order."""
        return self.list_actions(session_id=session_id, limit=10000)

    # ------------------------------------------------------------------
    # Spend
    # ------------------------------------------------------------------

    def get_spend(
        self, agent_id: str, period: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get spend for an agent. If period is None, returns today's spend."""
        self._ensure_tables()
        conn = self._db()
        try:
            if not period:
                period = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM guardrail_spend WHERE agent_id = ? AND period = ?",
                (agent_id, period),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return {
                "agent_id": agent_id, "period": period,
                "total_usd": 0.0, "action_count": 0, "denied_count": 0,
            }
        finally:
            conn.close()

    def get_total_spend(self, agent_id: str) -> float:
        """Get all-time spend for an agent."""
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(total_usd), 0) FROM guardrail_spend WHERE agent_id = ?",
                (agent_id,),
            )
            return cur.fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def create_approval(self, action_id: str, agent_id: str,
                        action_type: str, action_detail: Dict,
                        expires_minutes: int = 30) -> str:
        self._ensure_tables()
        conn = self._db()
        try:
            approval_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            from datetime import timedelta
            expires = (now + timedelta(minutes=expires_minutes)).isoformat()
            conn.execute(
                "INSERT INTO guardrail_approvals "
                "(id, action_id, agent_id, action_type, action_detail, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (approval_id, action_id, agent_id, action_type,
                 json.dumps(action_detail), expires, now.isoformat()),
            )
            conn.commit()
            return approval_id
        finally:
            conn.close()

    def decide_approval(self, approval_id: str, approved: bool, decided_by: str = "user") -> bool:
        self._ensure_tables()
        conn = self._db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE guardrail_approvals SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE id = ? AND status = 'pending'",
                ("approved" if approved else "denied", decided_by, now, approval_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def list_pending_approvals(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()
            query = "SELECT * FROM guardrail_approvals WHERE status = 'pending'"
            params: list = []
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            query += " ORDER BY created_at ASC"
            cur.execute(query, params)
            return [
                {**dict(row), "action_detail": json.loads(row["action_detail"] or "{}")}
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        self._ensure_tables()
        conn = self._db()
        try:
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM guardrail_agents WHERE enabled = 1")
            active_agents = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM guardrail_agents WHERE killed = 1")
            killed_agents = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM guardrail_policies WHERE enabled = 1")
            active_policies = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM guardrail_actions")
            total_actions = cur.fetchone()[0]

            cur.execute(
                "SELECT decision, COUNT(*) as cnt FROM guardrail_actions "
                "GROUP BY decision"
            )
            actions_by_decision = {row["decision"]: row["cnt"] for row in cur.fetchall()}

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cur.execute(
                "SELECT COUNT(*) FROM guardrail_actions WHERE created_at >= ?",
                (today,),
            )
            today_actions = cur.fetchone()[0]

            cur.execute(
                "SELECT COALESCE(SUM(total_usd), 0) FROM guardrail_spend WHERE period = ?",
                (today,),
            )
            today_spend = cur.fetchone()[0]

            cur.execute("SELECT COALESCE(SUM(total_usd), 0) FROM guardrail_spend")
            total_spend = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM guardrail_approvals WHERE status = 'pending'"
            )
            pending_approvals = cur.fetchone()[0]

            return {
                "active_agents": active_agents,
                "killed_agents": killed_agents,
                "active_policies": active_policies,
                "total_actions": total_actions,
                "actions_by_decision": actions_by_decision,
                "today_actions": today_actions,
                "today_spend_usd": round(today_spend, 4),
                "total_spend_usd": round(total_spend, 4),
                "pending_approvals": pending_approvals,
            }
        finally:
            conn.close()
