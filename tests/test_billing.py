"""
Tests for billing module — credit-based pay-per-evaluation with BTC payments.
"""

import os
import pytest
from unittest.mock import patch

from agent_guardrail.billing import (
    BillingManager,
    CREDIT_PACKS,
    FREE_TIER_DAILY,
    STATUS_CONFIRMED,
    STATUS_UNCONFIRMED,
)
from agent_guardrail.store import GuardrailStore


@pytest.fixture
def billing_store(tmp_path):
    """Store with billing tables initialized."""
    db_path = str(tmp_path / "test_billing.db")
    store = GuardrailStore(db_path=db_path)
    store._ensure_tables()
    return store


@pytest.fixture
def billing(billing_store):
    """BillingManager with billing enabled."""
    with patch.dict(os.environ, {"GUARDRAIL_BILLING_ENABLED": "true"}):
        mgr = BillingManager(db_path=billing_store.db_path)
        yield mgr


@pytest.fixture
def billing_with_secret(billing_store):
    """BillingManager with webhook secret configured."""
    with patch.dict(
        os.environ,
        {
            "GUARDRAIL_BILLING_ENABLED": "true",
            "BLOCKONOMICS_WEBHOOK_SECRET": "test_secret_123",
        },
    ):
        mgr = BillingManager(db_path=billing_store.db_path)
        yield mgr


@pytest.fixture
def agent_id(billing_store):
    """Register a test agent, return its ID."""
    result = billing_store.register_agent(name="billing-test-agent", framework="pytest")
    return result["id"]


# ------------------------------------------------------------------
# Free Tier
# ------------------------------------------------------------------


class TestFreeTier:
    """Free tier: 100 evals/day, resets daily."""

    def test_free_tier_allows_up_to_limit(self, billing, agent_id):
        """First 100 evals should be allowed via free tier."""
        for i in range(FREE_TIER_DAILY):
            allowed, reason = billing.check_and_deduct(agent_id)
            assert allowed, f"Eval {i+1} should be allowed"
            assert reason == "free_tier"

    def test_free_tier_blocks_after_limit(self, billing, agent_id):
        """101st eval without credits should be blocked."""
        for _ in range(FREE_TIER_DAILY):
            billing.check_and_deduct(agent_id)

        allowed, reason = billing.check_and_deduct(agent_id)
        assert not allowed
        assert reason == "no_credits"

    def test_free_tier_first_call_creates_record(self, billing, agent_id):
        """First eval for a new agent creates billing_credits record."""
        allowed, reason = billing.check_and_deduct(agent_id)
        assert allowed
        assert reason == "free_tier"

        balance = billing.get_balance(agent_id)
        assert balance["lifetime_evals"] == 1
        assert balance["free_remaining_today"] == FREE_TIER_DAILY - 1

    def test_free_tier_daily_reset(self, billing, agent_id):
        """Free tier resets when date changes."""
        # Use up all free evals
        for _ in range(FREE_TIER_DAILY):
            billing.check_and_deduct(agent_id)

        # Simulate date change by directly updating the reset date
        conn = billing._db()
        try:
            conn.execute(
                "UPDATE billing_credits SET free_reset_date = '2020-01-01' WHERE agent_id = ?",
                (agent_id,),
            )
            conn.commit()
        finally:
            conn.close()

        # Should be allowed again
        allowed, reason = billing.check_and_deduct(agent_id)
        assert allowed
        assert reason == "free_tier"


# ------------------------------------------------------------------
# Credit Deduction
# ------------------------------------------------------------------


class TestCreditDeduction:
    """Paid credits consumed after free tier exhausted."""

    def test_credits_used_after_free_tier(self, billing, agent_id):
        """When free tier is exhausted, paid credits are used."""
        # Grant 10 credits
        billing.grant_credits(agent_id, 10, "test")

        # Exhaust free tier
        for _ in range(FREE_TIER_DAILY):
            billing.check_and_deduct(agent_id)

        # Next eval should use paid credit
        allowed, reason = billing.check_and_deduct(agent_id)
        assert allowed
        assert reason == "credits"

        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 9

    def test_credits_fully_exhausted(self, billing, agent_id):
        """Blocked when both free tier and credits are gone."""
        billing.grant_credits(agent_id, 2, "test")

        # Exhaust free tier
        for _ in range(FREE_TIER_DAILY):
            billing.check_and_deduct(agent_id)

        # Use both credits
        billing.check_and_deduct(agent_id)
        billing.check_and_deduct(agent_id)

        # Now blocked
        allowed, reason = billing.check_and_deduct(agent_id)
        assert not allowed
        assert reason == "no_credits"

    def test_credit_deduction_creates_ledger_entry(self, billing, agent_id):
        """Each paid eval creates a ledger entry."""
        billing.grant_credits(agent_id, 5, "test")

        # Exhaust free tier
        for _ in range(FREE_TIER_DAILY):
            billing.check_and_deduct(agent_id)

        # Use a paid credit
        billing.check_and_deduct(agent_id)

        ledger = billing.get_ledger(agent_id)
        # Should have grant entry + eval deduction entry
        eval_entries = [e for e in ledger if e["reason"] == "eval"]
        assert len(eval_entries) == 1
        assert eval_entries[0]["delta"] == -1


# ------------------------------------------------------------------
# Balance
# ------------------------------------------------------------------


class TestBalance:
    """Balance reporting."""

    def test_balance_new_agent(self, billing, agent_id):
        """New agent has full free tier and zero credits."""
        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 0
        assert balance["free_remaining_today"] == FREE_TIER_DAILY
        assert balance["lifetime_evals"] == 0

    def test_balance_after_grant(self, billing, agent_id):
        """Balance reflects granted credits."""
        billing.grant_credits(agent_id, 500, "test")
        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 500


# ------------------------------------------------------------------
# Webhook
# ------------------------------------------------------------------


class TestWebhook:
    """Blockonomics webhook processing."""

    def _create_pending_payment(self, billing, agent_id):
        """Helper: insert a pending payment directly."""
        import uuid
        from datetime import datetime, timezone, timedelta

        payment_id = str(uuid.uuid4())
        btc_address = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
        now = datetime.now(timezone.utc)

        conn = billing._db()
        try:
            conn.execute(
                "INSERT INTO billing_payments "
                "(id, agent_id, btc_address, pack_id, credits, price_usd, "
                "price_btc, price_satoshi, status, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pack_1000', 1000, 10.0, "
                "0.00015, 15000, 'pending', ?, ?, ?)",
                (
                    payment_id,
                    agent_id,
                    btc_address,
                    (now + timedelta(hours=24)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return payment_id, btc_address

    def test_webhook_grants_credits_on_confirmed(self, billing_with_secret, agent_id):
        """status=2 grants credits to agent."""
        billing = billing_with_secret
        payment_id, addr = self._create_pending_payment(billing, agent_id)

        result = billing.handle_webhook(
            addr=addr,
            status=STATUS_CONFIRMED,
            value=15000,
            txid="abc123txid",
            secret="test_secret_123",
        )

        assert result["status"] == "confirmed"
        assert result["credits_granted"] == 1000

        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 1000

    def test_webhook_rejects_wrong_secret(self, billing_with_secret, agent_id):
        """Wrong secret is rejected."""
        billing = billing_with_secret
        _, addr = self._create_pending_payment(billing, agent_id)

        result = billing.handle_webhook(
            addr=addr,
            status=STATUS_CONFIRMED,
            value=15000,
            txid="abc123txid",
            secret="wrong_secret",
        )

        assert result["error"] == "invalid_secret"

    def test_webhook_idempotent_on_double_confirm(self, billing_with_secret, agent_id):
        """Double confirmation doesn't double-grant credits."""
        billing = billing_with_secret
        _, addr = self._create_pending_payment(billing, agent_id)

        # First confirmation
        billing.handle_webhook(
            addr=addr,
            status=STATUS_CONFIRMED,
            value=15000,
            txid="abc123txid",
            secret="test_secret_123",
        )

        # Second confirmation
        result = billing.handle_webhook(
            addr=addr,
            status=STATUS_CONFIRMED,
            value=15000,
            txid="abc123txid",
            secret="test_secret_123",
        )

        assert result["status"] == "already_confirmed"

        # Credits should still be 1000, not 2000
        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 1000

    def test_webhook_unconfirmed_updates_status(self, billing_with_secret, agent_id):
        """status=0 updates payment to unconfirmed without granting credits."""
        billing = billing_with_secret
        _, addr = self._create_pending_payment(billing, agent_id)

        result = billing.handle_webhook(
            addr=addr,
            status=STATUS_UNCONFIRMED,
            value=15000,
            txid="abc123txid",
            secret="test_secret_123",
        )

        assert result["status"] == "unconfirmed"
        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 0

    def test_webhook_unknown_address(self, billing_with_secret, agent_id):
        """Webhook for unknown address returns error."""
        result = billing_with_secret.handle_webhook(
            addr="unknown_address_xyz",
            status=STATUS_CONFIRMED,
            value=15000,
            txid="abc123txid",
            secret="test_secret_123",
        )
        assert result["error"] == "unknown_address"


# ------------------------------------------------------------------
# Admin Grant
# ------------------------------------------------------------------


class TestAdminGrant:
    """Manual credit grants by admin."""

    def test_grant_adds_credits(self, billing, agent_id):
        """Admin grant adds credits to agent balance."""
        result = billing.grant_credits(agent_id, 500, "promo")
        assert result["granted"] == 500
        assert result["balance"]["credit_balance"] == 500

    def test_grant_creates_ledger_entry(self, billing, agent_id):
        """Grant creates a ledger entry with reason."""
        billing.grant_credits(agent_id, 250, "support_ticket")
        ledger = billing.get_ledger(agent_id)
        assert len(ledger) == 1
        assert ledger[0]["delta"] == 250
        assert ledger[0]["reason"] == "support_ticket"

    def test_grant_to_new_agent_creates_record(self, billing, agent_id):
        """Granting to agent with no billing record creates one."""
        billing.grant_credits(agent_id, 100, "test")
        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 100

    def test_multiple_grants_accumulate(self, billing, agent_id):
        """Multiple grants stack."""
        billing.grant_credits(agent_id, 100, "first")
        billing.grant_credits(agent_id, 200, "second")
        balance = billing.get_balance(agent_id)
        assert balance["credit_balance"] == 300


# ------------------------------------------------------------------
# Billing Disabled
# ------------------------------------------------------------------


class TestBillingDisabled:
    """When billing is disabled, all evals proceed."""

    def test_disabled_always_allows(self, billing_store, agent_id):
        """With billing disabled, check_and_deduct always returns True."""
        with patch.dict(os.environ, {"GUARDRAIL_BILLING_ENABLED": "false"}):
            mgr = BillingManager(db_path=billing_store.db_path)
            for _ in range(200):  # Way past free tier
                allowed, reason = mgr.check_and_deduct(agent_id)
                assert allowed
                assert reason == "billing_disabled"


# ------------------------------------------------------------------
# Credit Packs
# ------------------------------------------------------------------


class TestCreditPacks:
    """Credit pack definitions."""

    def test_all_packs_have_required_fields(self):
        for pack_id, pack in CREDIT_PACKS.items():
            assert pack["id"] == pack_id
            assert pack["credits"] > 0
            assert pack["price_usd"] > 0
            assert "label" in pack

    def test_volume_discount(self):
        """Larger packs have lower per-eval cost."""
        costs = []
        for pack in sorted(CREDIT_PACKS.values(), key=lambda p: p["credits"]):
            costs.append(pack["price_usd"] / pack["credits"])
        # Each subsequent pack should be cheaper per eval
        for i in range(1, len(costs)):
            assert costs[i] < costs[i - 1]


# ------------------------------------------------------------------
# Ledger
# ------------------------------------------------------------------


class TestLedger:
    """Transaction history."""

    def test_ledger_empty_for_new_agent(self, billing, agent_id):
        assert billing.get_ledger(agent_id) == []

    def test_ledger_ordered_by_created_at_desc(self, billing, agent_id):
        billing.grant_credits(agent_id, 100, "first")
        billing.grant_credits(agent_id, 200, "second")
        ledger = billing.get_ledger(agent_id)
        assert len(ledger) == 2
        assert ledger[0]["reason"] == "second"  # Most recent first
        assert ledger[1]["reason"] == "first"

    def test_ledger_tracks_balance_after(self, billing, agent_id):
        billing.grant_credits(agent_id, 100, "first")
        billing.grant_credits(agent_id, 200, "second")
        ledger = billing.get_ledger(agent_id)
        assert ledger[0]["balance_after"] == 300
        assert ledger[1]["balance_after"] == 100


# ------------------------------------------------------------------
# Payment Status
# ------------------------------------------------------------------


class TestPaymentStatus:
    """Payment polling."""

    def test_nonexistent_payment_returns_none(self, billing):
        assert billing.get_payment_status("nonexistent-id") is None
