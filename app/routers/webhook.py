"""
============================================================
🔒 RAZORPAY WEBHOOK
============================================================
Safety net — Razorpay calls this URL when payment succeeds/fails.
Even if customer's browser crashes after paying, this ensures
wallet gets credited.

HOW TO CONFIGURE IN RAZORPAY DASHBOARD:
  1. Go to https://dashboard.razorpay.com/app/webhooks
  2. Click "Add New Webhook"
  3. URL: https://yourdomain.com/api/v1/webhook/razorpay
  4. Secret: (add a secret string, same as RAZORPAY_WEBHOOK_SECRET in .env)
  5. Events: Select "payment.authorized" and "payment.failed"
  6. Click "Create Webhook"

FOR LOCAL TESTING:
  Use ngrok: ngrok http 8000
  Then use the ngrok URL in Razorpay dashboard
============================================================
"""

from fastapi import APIRouter, Request, HTTPException
from app.database import execute_query
from app.helpers.wallet_helper import credit_wallet, ensure_wallet_exists
from app.helpers.transaction_helper import record_add_money
from app.helpers.payment_helper import (
    mark_payment_success, mark_payment_failed,
    mark_webhook_verified, is_payment_already_credited,
    get_payment_by_order_id
)
import logging
import hmac
import hashlib
import json
import os

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhook"])


def verify_webhook_signature(request_body: bytes, signature: str, secret: str) -> bool:
    """
    Verify that the webhook request actually came from Razorpay.
    
    Razorpay signs every webhook with your webhook secret.
    If someone tries to fake a webhook call, this will catch it.
    
    Args:
        request_body: Raw bytes from the request
        signature: X-Razorpay-Signature header value
        secret: Your RAZORPAY_WEBHOOK_SECRET from .env
    
    Returns:
        True if valid, False if tampered
    """
    if not signature or not secret:
        return False
    
    expected = hmac.new(
        secret.encode("utf-8"),
        request_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected, signature)


@router.post("/razorpay")
async def razorpay_webhook(request: Request):
    """
    Razorpay sends POST here when payment status changes.
    
    Events we handle:
      - payment.authorized  → Customer paid successfully
      - payment.failed      → Payment failed
    
    Flow:
      1. Verify signature (is this really from Razorpay?)
      2. Extract payment details
      3. Check if already credited (idempotency)
      4. Credit wallet if not already done
      5. Mark webhook_verified = 1
    
    Returns:
      Always return 200 OK — Razorpay retries on non-200
    """
    try:
        # 1. Read raw body
        body = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")
        webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
        
        # 2. Verify signature
        if webhook_secret and not verify_webhook_signature(body, signature, webhook_secret):
            logger.warning("❌ Webhook signature verification failed — possible tampering")
            # Still return 200 to prevent Razorpay from retrying
            return {"status": "signature_failed"}
        
        # 3. Parse payload
        payload = json.loads(body)
        event = payload.get("event", "")
        
        logger.info(f"🔔 Razorpay webhook received: {event}")
        
        # 4. Handle payment.authorized (successful payment)
        if event == "payment.authorized":
            return await handle_payment_authorized(payload)
        
        # 5. Handle payment.failed
        elif event == "payment.failed":
            return await handle_payment_failed(payload)
        
        # 6. Other events — log and ignore
        else:
            logger.info(f"🔔 Webhook event ignored: {event}")
            return {"status": "ignored", "event": event}
    
    except Exception as e:
        logger.error(f"❌ Webhook processing error: {e}")
        # IMPORTANT: Always return 200, otherwise Razorpay keeps retrying
        return {"status": "error", "message": str(e)}


async def handle_payment_authorized(payload: dict) -> dict:
    """
    Handle successful payment from Razorpay webhook.
    
    This runs EVEN IF verify-payment API already credited the wallet.
    Idempotency check prevents double-credit.
    """
    try:
        # Extract payment details from Razorpay payload
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        
        razorpay_payment_id = payment_entity.get("id", "")           # pay_xxxxx
        razorpay_order_id = payment_entity.get("order_id", "")       # order_xxxxx
        amount_paise = payment_entity.get("amount", 0)               # in paise
        amount_inr = round(amount_paise / 100, 2)                    # convert to INR
        status = payment_entity.get("status", "")
        notes = payment_entity.get("notes", {})
        user_id_str = notes.get("user_id", "")
        
        logger.info(
            f"🔔 Payment authorized: {razorpay_payment_id} | "
            f"Order: {razorpay_order_id} | ₹{amount_inr} | User: {user_id_str}"
        )
        
        # Validate user_id
        if not user_id_str:
            logger.warning(f"⚠️ Webhook: No user_id in payment notes for {razorpay_payment_id}")
            # Try to find user from payments table
            payment_record = get_payment_by_order_id(razorpay_order_id)
            if payment_record:
                user_id_str = str(payment_record["user_id"])
            else:
                logger.error(f"❌ Cannot find user for payment {razorpay_payment_id}")
                return {"status": "error", "message": "user_id not found"}
        
        user_id = int(user_id_str)
        
        # ✅ IDEMPOTENCY CHECK — prevent double credit
        if is_payment_already_credited(razorpay_order_id):
            # Already credited by verify-payment API — just mark webhook verified
            mark_webhook_verified(razorpay_order_id)
            logger.info(f"✅ Webhook: Payment already credited, marked webhook_verified: {razorpay_order_id}")
            return {"status": "already_credited"}
        
        # Also check transactions table
        existing_txn = execute_query(
            "SELECT id FROM transactions WHERE reference_id = %s",
            (razorpay_payment_id,),
            fetch_one=True
        )
        if existing_txn:
            mark_webhook_verified(razorpay_order_id)
            logger.info(f"✅ Webhook: Transaction already exists for {razorpay_payment_id}")
            return {"status": "already_credited"}
        
        # ✅ NOT YET CREDITED — credit wallet now (browser must have closed)
        logger.info(f"⚠️ Webhook: Crediting wallet (browser didn't complete) — User {user_id} | ₹{amount_inr}")
        
        # Mark payment success
        mark_payment_success(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature="webhook_verified"  # no signature from webhook
        )
        
        # Credit wallet
        credit_wallet(user_id, amount_inr, update_total_added=True)
        
        # Record transaction
        record_add_money(
            user_id=user_id,
            amount=amount_inr,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=razorpay_order_id
        )
        
        # Mark webhook verified
        mark_webhook_verified(razorpay_order_id)
        
        logger.info(f"✅ Webhook: Wallet credited successfully — User {user_id} | ₹{amount_inr}")
        return {"status": "credited"}
    
    except Exception as e:
        logger.error(f"❌ Webhook handle_payment_authorized error: {e}")
        return {"status": "error", "message": str(e)}


async def handle_payment_failed(payload: dict) -> dict:
    """
    Handle failed payment from Razorpay webhook.
    
    Just marks the payment as failed in our DB.
    No wallet changes needed (money was never credited).
    """
    try:
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        
        razorpay_payment_id = payment_entity.get("id", "")
        razorpay_order_id = payment_entity.get("order_id", "")
        error_reason = payment_entity.get("error_reason", "unknown")
        
        logger.info(f"❌ Payment failed: {razorpay_payment_id} | Reason: {error_reason}")
        
        # Mark payment as failed
        mark_payment_failed(razorpay_order_id, reason=error_reason)
        
        return {"status": "noted"}
    
    except Exception as e:
        logger.error(f"❌ Webhook handle_payment_failed error: {e}")
        return {"status": "error", "message": str(e)}