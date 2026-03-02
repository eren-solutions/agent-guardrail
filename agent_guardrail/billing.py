"""
Billing Module — BTC Payments via Blockonomics
===============================================

Pay-per-evaluation credits with free tier.
Non-custodial BTC payments — no KYC, no traditional finance.

Pricing:
    Free tier:  100 evaluations/day per agent (resets daily UTC)
    pack_1000:  1,000 evals — $10  ($0.010/eval)
    pack_5000:  5,000 evals — $40  ($0.008/eval)
    pack_25000: 25,000 evals — $150 ($0.006/eval)

Graceful disable: if BLOCKONOMICS_API_KEY is not set, billing is off
and all evaluations proceed without metering (backward compatible).
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

FREE_TIER_DAILY = 100

CREDIT_PACKS: Dict[str, Dict[str, Any]] = {
    "pack_1000": {
        "id": "pack_1000",
        "credits": 1000,
        "price_usd": 10.0,
        "label": "1,000 evaluations",
        "per_eval": "$0.010",
    },
    "pack_5000": {
        "id": "pack_5000",
        "credits": 5000,
        "price_usd": 40.0,
        "label": "5,000 evaluations",
        "per_eval": "$0.008",
    },
    "pack_25000": {
        "id": "pack_25000",
        "credits": 25000,
        "price_usd": 150.0,
        "label": "25,000 evaluations",
        "per_eval": "$0.006",
    },
}

# Payment status constants (Blockonomics)
STATUS_UNCONFIRMED = 0
STATUS_PARTIALLY_CONFIRMED = 1
STATUS_CONFIRMED = 2

# Payment expiry (24 hours)
PAYMENT_EXPIRY_MINUTES = 1440


class BillingManager:
    """Manages credit-based billing with Blockonomics BTC payments."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._api_key = os.environ.get("BLOCKONOMICS_API_KEY", "").strip()
        self._webhook_secret = os.environ.get("BLOCKONOMICS_WEBHOOK_SECRET", "").strip()

    @property
    def enabled(self) -> bool:
        flag = os.environ.get("GUARDRAIL_BILLING_ENABLED", "true").lower()
        return flag not in ("false", "0", "no")

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Credit checks
    # ------------------------------------------------------------------

    def check_and_deduct(self, agent_id: str) -> Tuple[bool, str]:
        """Atomic check + deduct. Returns (allowed, reason).

        Uses BEGIN IMMEDIATE for SQLite thread safety.
        """
        if not self.enabled:
            return True, "billing_disabled"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT credit_balance, free_used_today, free_reset_date "
                "FROM billing_credits WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

            if row is None:
                # First time — create record, use free tier
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO billing_credits "
                    "(agent_id, credit_balance, free_used_today, free_reset_date, "
                    "lifetime_evals, created_at, updated_at) "
                    "VALUES (?, 0, 1, ?, 1, ?, ?)",
                    (agent_id, today, now, now),
                )
                conn.commit()
                return True, "free_tier"

            balance = row["credit_balance"]
            free_used = row["free_used_today"]
            reset_date = row["free_reset_date"]

            # Reset free tier if new day
            if reset_date != today:
                free_used = 0

            now = datetime.now(timezone.utc).isoformat()

            # Try free tier first
            if free_used < FREE_TIER_DAILY:
                conn.execute(
                    "UPDATE billing_credits SET "
                    "free_used_today = ?, free_reset_date = ?, "
                    "lifetime_evals = lifetime_evals + 1, updated_at = ? "
                    "WHERE agent_id = ?",
                    (free_used + 1, today, now, agent_id),
                )
                conn.commit()
                return True, "free_tier"

            # Try paid credits
            if balance > 0:
                conn.execute(
                    "UPDATE billing_credits SET "
                    "credit_balance = credit_balance - 1, "
                    "lifetime_evals = lifetime_evals + 1, updated_at = ? "
                    "WHERE agent_id = ?",
                    (now, agent_id),
                )
                # Ledger entry
                new_balance = balance - 1
                conn.execute(
                    "INSERT INTO billing_ledger "
                    "(id, agent_id, delta, reason, balance_after, created_at) "
                    "VALUES (?, ?, -1, 'eval', ?, ?)",
                    (str(uuid.uuid4()), agent_id, new_balance, now),
                )
                conn.commit()
                return True, "credits"

            conn.rollback()
            return False, "no_credits"
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_balance(self, agent_id: str) -> Dict[str, Any]:
        """Get credit balance, free tier remaining, and lifetime evals."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT credit_balance, free_used_today, free_reset_date, lifetime_evals "
                "FROM billing_credits WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

            if row is None:
                return {
                    "credit_balance": 0,
                    "free_remaining_today": FREE_TIER_DAILY,
                    "free_tier_daily": FREE_TIER_DAILY,
                    "lifetime_evals": 0,
                }

            free_used = row["free_used_today"]
            if row["free_reset_date"] != today:
                free_used = 0

            return {
                "credit_balance": row["credit_balance"],
                "free_remaining_today": max(0, FREE_TIER_DAILY - free_used),
                "free_tier_daily": FREE_TIER_DAILY,
                "lifetime_evals": row["lifetime_evals"],
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Checkout (Blockonomics)
    # ------------------------------------------------------------------

    def create_checkout(self, agent_id: str, pack_id: str) -> Dict[str, Any]:
        """Create a BTC payment via Blockonomics. Returns address + amount."""
        if pack_id not in CREDIT_PACKS:
            raise ValueError(f"Unknown pack: {pack_id}")

        pack = CREDIT_PACKS[pack_id]
        price_usd = pack["price_usd"]

        # Get BTC price from Blockonomics
        btc_price = self._blockonomics_get_price()
        price_btc = round(price_usd / btc_price, 8)
        price_satoshi = int(price_btc * 1e8)

        # Get fresh BTC address from Blockonomics (xpub-derived)
        btc_address = self._blockonomics_new_address()

        # Store payment record
        payment_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        from datetime import timedelta

        expires_at = (now + timedelta(minutes=PAYMENT_EXPIRY_MINUTES)).isoformat()

        conn = self._db()
        try:
            conn.execute(
                "INSERT INTO billing_payments "
                "(id, agent_id, btc_address, pack_id, credits, price_usd, "
                "price_btc, price_satoshi, status, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (
                    payment_id,
                    agent_id,
                    btc_address,
                    pack_id,
                    pack["credits"],
                    price_usd,
                    price_btc,
                    price_satoshi,
                    expires_at,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "payment_id": payment_id,
            "btc_address": btc_address,
            "amount_btc": price_btc,
            "amount_satoshi": price_satoshi,
            "price_usd": price_usd,
            "pack": pack,
            "expires_at": expires_at,
        }

    def get_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Get payment details by ID."""
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT * FROM billing_payments WHERE id = ?",
                (payment_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Webhook (Blockonomics callback)
    # ------------------------------------------------------------------

    def handle_webhook(
        self,
        addr: str,
        status: int,
        value: int,
        txid: str,
        secret: str,
    ) -> Dict[str, Any]:
        """Process Blockonomics payment callback.

        Args:
            addr: BTC address that received payment
            status: 0=unconfirmed, 1=partial, 2=confirmed
            value: Amount in satoshi
            txid: Bitcoin transaction ID
            secret: Webhook secret for verification
        """
        # Verify webhook secret
        if not self._webhook_secret or secret != self._webhook_secret:
            logger.warning("Webhook secret mismatch for addr=%s", addr)
            return {"error": "invalid_secret"}

        conn = self._db()
        try:
            row = conn.execute(
                "SELECT * FROM billing_payments WHERE btc_address = ?",
                (addr,),
            ).fetchone()

            if not row:
                logger.warning("Webhook for unknown address: %s", addr)
                return {"error": "unknown_address"}

            payment = dict(row)

            # Idempotency: already confirmed
            if payment["status"] == "confirmed":
                logger.info("Duplicate confirmation for payment %s", payment["id"])
                return {"status": "already_confirmed", "payment_id": payment["id"]}

            now = datetime.now(timezone.utc).isoformat()

            if status == STATUS_CONFIRMED:
                # Mark payment confirmed
                conn.execute(
                    "UPDATE billing_payments SET status = 'confirmed', "
                    "txid = ?, updated_at = ? WHERE id = ?",
                    (txid, now, payment["id"]),
                )

                # Grant credits
                self._grant_credits_internal(
                    conn,
                    payment["agent_id"],
                    payment["credits"],
                    f"pack:{payment['pack_id']}",
                    payment["id"],
                )
                conn.commit()

                logger.info(
                    "Payment confirmed: %s credits for agent %s (txid=%s)",
                    payment["credits"],
                    payment["agent_id"],
                    txid,
                )
                return {
                    "status": "confirmed",
                    "payment_id": payment["id"],
                    "credits_granted": payment["credits"],
                }

            elif status == STATUS_UNCONFIRMED:
                conn.execute(
                    "UPDATE billing_payments SET status = 'unconfirmed', "
                    "txid = ?, updated_at = ? WHERE id = ?",
                    (txid, now, payment["id"]),
                )
                conn.commit()
                return {"status": "unconfirmed", "payment_id": payment["id"]}

            elif status == STATUS_PARTIALLY_CONFIRMED:
                conn.execute(
                    "UPDATE billing_payments SET status = 'partially_confirmed', "
                    "txid = ?, updated_at = ? WHERE id = ?",
                    (txid, now, payment["id"]),
                )
                conn.commit()
                return {"status": "partially_confirmed", "payment_id": payment["id"]}

            return {"status": "unknown", "blockonomics_status": status}

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Admin: grant credits
    # ------------------------------------------------------------------

    def grant_credits(self, agent_id: str, amount: int, reason: str = "admin_grant") -> Dict:
        """Admin method to manually grant credits."""
        conn = self._db()
        try:
            self._grant_credits_internal(conn, agent_id, amount, reason)
            conn.commit()
            balance = self.get_balance(agent_id)
            return {"granted": amount, "reason": reason, "balance": balance}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _grant_credits_internal(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        amount: int,
        reason: str,
        payment_id: Optional[str] = None,
    ) -> None:
        """Internal: grant credits within an existing transaction."""
        now = datetime.now(timezone.utc).isoformat()

        row = conn.execute(
            "SELECT credit_balance FROM billing_credits WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()

        if row is None:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            new_balance = amount
            conn.execute(
                "INSERT INTO billing_credits "
                "(agent_id, credit_balance, free_used_today, free_reset_date, "
                "lifetime_evals, created_at, updated_at) "
                "VALUES (?, ?, 0, ?, 0, ?, ?)",
                (agent_id, amount, today, now, now),
            )
        else:
            new_balance = row["credit_balance"] + amount
            conn.execute(
                "UPDATE billing_credits SET credit_balance = ?, updated_at = ? "
                "WHERE agent_id = ?",
                (new_balance, now, agent_id),
            )

        # Ledger entry
        conn.execute(
            "INSERT INTO billing_ledger "
            "(id, agent_id, delta, reason, balance_after, payment_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), agent_id, amount, reason, new_balance, payment_id, now),
        )

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    def get_ledger(self, agent_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get transaction history for an agent."""
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT * FROM billing_ledger WHERE agent_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Blockonomics HTTP helpers (stdlib only)
    # ------------------------------------------------------------------

    def _blockonomics_request(self, path: str, method: str = "GET", data: Optional[dict] = None):
        """Make an authenticated request to Blockonomics API."""
        url = f"https://www.blockonomics.co{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, headers=headers, method=method)

        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            logger.error("Blockonomics API error %s: %s", e.code, body_text)
            raise RuntimeError(f"Blockonomics API error {e.code}: {body_text}") from e
        except URLError as e:
            logger.error("Blockonomics network error: %s", e.reason)
            raise RuntimeError(f"Blockonomics network error: {e.reason}") from e

    def _blockonomics_new_address(self) -> str:
        """Get a new BTC address derived from merchant's xpub."""
        result = self._blockonomics_request("/api/new_address", method="POST")
        return result["address"]

    def _blockonomics_get_price(self) -> float:
        """Get current BTC price in USD."""
        result = self._blockonomics_request("/api/price?currency=USD")
        return float(result["price"])
