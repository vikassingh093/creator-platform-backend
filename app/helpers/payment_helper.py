"""
============================================================
💳 PAYMENT HELPER
============================================================
Tracks all Razorpay payment details in the payments table.

USAGE:
  from app.helpers.payment_helper import create_payment_record, mark_payment_success

WHY:
  - Payments table stores Razorpay order_id, payment_id, signature
  - If customer disputes, you can show Razorpay proof
  - Webhook can verify and credit even if browser closed
  - Admin can see all payment attempts (success + failed)
============================================================
"""

from app.database import execute_query
import logging
import uuid

logger = logging.getLogger(__name__)


def create_payment_record(
    user_id: int,
    amount: float,
    razorpay_order_id: str
) -> str:
    """
    Create a pending payment record when Razorpay order is created.
    
    Called from wallet.py → create-order endpoint.
    
    Args:
        user_id: Customer ID
        amount: Payment amount in INR
        razorpay_order_id: Razorpay order ID (order_xxxxx)
    
    Returns:
        merchant_transaction_id (unique ID for this payment)
    """
    merchant_txn_id = f"pay_{user_id}_{uuid.uuid4().hex[:12]}"
    
    try:
        execute_query(
            """
            INSERT INTO payments 
                (user_id, merchant_transaction_id, amount, status, 
                 razorpay_order_id, payment_gateway, credited_to_wallet, webhook_verified)
            VALUES (%s, %s, %s, 'pending', %s, 'razorpay', 0, 0)
            """,
            (user_id, merchant_txn_id, amount, razorpay_order_id)
        )
        
        logger.info(f"💳 Payment record created: {merchant_txn_id} | Order: {razorpay_order_id}")
        return merchant_txn_id
        
    except Exception as e:
        logger.error(f"❌ Failed to create payment record: {e}")
        raise


def mark_payment_success(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str
) -> dict:
    """
    Mark payment as success after signature verification.
    
    Called from wallet.py → verify-payment endpoint.
    
    Returns:
        Payment record dict, or None if not found
    """
    try:
        # Find the payment record
        payment = execute_query(
            "SELECT * FROM payments WHERE razorpay_order_id = %s",
            (razorpay_order_id,),
            fetch_one=True
        )
        
        if not payment:
            logger.warning(f"⚠️ Payment not found for order: {razorpay_order_id}")
            return None
        
        # Check if already credited (idempotency)
        if payment.get("credited_to_wallet") == 1:
            logger.warning(f"⚠️ Payment already credited: {razorpay_order_id}")
            return payment
        
        # Update payment record
        execute_query(
            """
            UPDATE payments 
            SET status = 'success',
                razorpay_payment_id = %s,
                razorpay_signature = %s,
                credited_to_wallet = 1
            WHERE razorpay_order_id = %s
            """,
            (razorpay_payment_id, razorpay_signature, razorpay_order_id)
        )
        
        logger.info(f"✅ Payment marked success: {razorpay_order_id} → {razorpay_payment_id}")
        
        return execute_query(
            "SELECT * FROM payments WHERE razorpay_order_id = %s",
            (razorpay_order_id,),
            fetch_one=True
        )
        
    except Exception as e:
        logger.error(f"❌ Failed to mark payment success: {e}")
        raise


def mark_payment_failed(razorpay_order_id: str, reason: str = None) -> None:
    """
    Mark payment as failed.
    
    Called when Razorpay callback indicates failure.
    """
    try:
        execute_query(
            "UPDATE payments SET status = 'failed' WHERE razorpay_order_id = %s",
            (razorpay_order_id,)
        )
        logger.info(f"❌ Payment marked failed: {razorpay_order_id} | Reason: {reason}")
    except Exception as e:
        logger.error(f"❌ Failed to mark payment failed: {e}")


def mark_webhook_verified(razorpay_order_id: str) -> None:
    """
    Mark that this payment was verified by Razorpay webhook.
    
    Extra safety: even if verify-payment API was called by user,
    webhook confirmation gives double assurance.
    """
    try:
        execute_query(
            "UPDATE payments SET webhook_verified = 1 WHERE razorpay_order_id = %s",
            (razorpay_order_id,)
        )
        logger.info(f"🔒 Webhook verified: {razorpay_order_id}")
    except Exception as e:
        logger.error(f"❌ Webhook verify failed: {e}")


def is_payment_already_credited(razorpay_order_id: str) -> bool:
    """
    Check if this payment was already credited to wallet.
    
    Prevents double-credit from both verify-payment API and webhook.
    """
    payment = execute_query(
        "SELECT credited_to_wallet FROM payments WHERE razorpay_order_id = %s",
        (razorpay_order_id,),
        fetch_one=True
    )
    return payment and payment.get("credited_to_wallet") == 1


def get_payment_by_order_id(razorpay_order_id: str) -> dict:
    """
    Get payment record by Razorpay order ID.
    """
    return execute_query(
        "SELECT * FROM payments WHERE razorpay_order_id = %s",
        (razorpay_order_id,),
        fetch_one=True
    )