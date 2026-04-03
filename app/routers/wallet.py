from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
import logging
import os
import hmac
import hashlib

# ✅ Import helpers — all logic lives there
from app.helpers.transaction_helper import record_add_money
from app.helpers.wallet_helper import (
    ensure_wallet_exists, get_balance, credit_wallet, 
    debit_wallet, has_sufficient_balance
)
from app.helpers.payment_helper import (
    create_payment_record, mark_payment_success,
    is_payment_already_credited
)
from app.helpers.offer_helper import check_and_apply_bonus

# ✅ Lazy import so server won't crash if razorpay missing
try:
    import razorpay
except ImportError:
    razorpay = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wallet", tags=["Wallet"])


# ── Models ────────────────────────────────────────────────
class AddMoneyRequest(BaseModel):
    amount: float
    payment_id: Optional[str] = None
    promo_code: Optional[str] = None

class DeductRequest(BaseModel):
    amount: float
    description: str
    type: str = "chat"
    reference_id: Optional[str] = None

class CreateOrderRequest(BaseModel):
    amount: float
    promo_code: Optional[str] = None  # ← User can pass promo code at order creation

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    amount: float
    promo_code: Optional[str] = None  # ← Promo code passed during verification


# ── GET wallet ────────────────────────────────────────────
@router.get("/")
def get_wallet(current_user: dict = Depends(get_current_user)):
    wallet = ensure_wallet_exists(current_user["id"])
    return {"success": True, "wallet": wallet}


# ── GET transactions ──────────────────────────────────────
@router.get("/transactions")
def get_transactions(current_user: dict = Depends(get_current_user)):
    transactions = execute_query(
        """
        SELECT * FROM transactions
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (current_user["id"],),
        fetch_all=True
    )
    return {"success": True, "transactions": transactions or []}


# ── POST add-money (⚠️ ADMIN-ONLY — manual wallet adjustment) ─
@router.post("/add-money")
def add_money(
    body: AddMoneyRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        # 🔴 FIX: Only admin can use this endpoint (no payment verification here)
        if current_user.get("user_type") != "admin":
            raise HTTPException(status_code=403, detail="This endpoint is restricted. Use /create-order for payments.")

        if body.amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid amount")
        if body.amount < 10:
            raise HTTPException(status_code=400, detail="Minimum recharge is ₹10")
        if body.amount > 10000:
            raise HTTPException(status_code=400, detail="Maximum recharge is ₹10,000")

        # ✅ Idempotency check — prevent double credit
        if body.payment_id:
            existing = execute_query(
                "SELECT id FROM transactions WHERE reference_id = %s",
                (body.payment_id,),
                fetch_one=True
            )
            if existing:
                raise HTTPException(status_code=400, detail="Payment already processed")

        # ✅ Credit wallet using helper
        wallet = credit_wallet(current_user["id"], body.amount, update_total_added=True)

        # ✅ Record transaction using helper
        record_add_money(
            user_id=current_user["id"],
            amount=body.amount,
            razorpay_payment_id=body.payment_id
        )

        # ✅ Get the transaction ID we just created
        txn = execute_query(
            "SELECT id FROM transactions WHERE user_id = %s AND type = 'add_money' ORDER BY id DESC LIMIT 1",
            (current_user["id"],), fetch_one=True
        )
        transaction_id = txn["id"] if txn else None

        # ✅ Check and apply bonus (promo code / first deposit / event / signup)
        bonus = check_and_apply_bonus(
            user_id=current_user["id"],
            deposit_amount=body.amount,
            deposit_txn_id=transaction_id,
            promo_code=body.promo_code
        )

        bonus_msg = ""
        if bonus:
            bonus_msg = f" + ₹{bonus['bonus_amount']} bonus!"
            logger.info(f"🎁 Bonus applied: {bonus['type']} — ₹{bonus['bonus_amount']} for user {current_user['id']}")

        logger.info(f"✅ Wallet credited: User {current_user['id']} | Amount ₹{body.amount}{bonus_msg}")
        return {
            "success": True, 
            "message": f"₹{body.amount} added successfully!{bonus_msg}", 
            "wallet": ensure_wallet_exists(current_user["id"]),  # Re-fetch to include bonus
            "bonus": bonus
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Add money error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


# ── POST create-order (Razorpay Step 1) ──────────────────
@router.post("/create-order")
def create_order(
    body: CreateOrderRequest,
    current_user: dict = Depends(get_current_user)
):
    if razorpay is None:
        raise HTTPException(status_code=500, detail="Razorpay not installed. Run: pip install razorpay")
    if body.amount < 10:
        raise HTTPException(status_code=400, detail="Minimum recharge is ₹10")
    if body.amount > 10000:
        raise HTTPException(status_code=400, detail="Maximum recharge is ₹10,000")
    try:
        client = razorpay.Client(auth=(
            os.getenv("RAZORPAY_KEY_ID"),
            os.getenv("RAZORPAY_KEY_SECRET")
        ))

        # ✅ Store promo code in notes so we can retrieve during verification
        notes = {
            "user_id": str(current_user["id"]),
            "user_name": current_user.get("name", "")
        }
        if body.promo_code:
            notes["promo_code"] = body.promo_code.strip().upper()

        order = client.order.create({
            "amount": int(body.amount * 100),
            "currency": "INR",
            "receipt": f"user_{current_user['id']}",
            "notes": notes
        })

        # ✅ Store payment record in payments table
        merchant_txn_id = create_payment_record(
            user_id=current_user["id"],
            amount=body.amount,
            razorpay_order_id=order["id"]
        )

        # ✅ Store promo code against this order for later use
        if body.promo_code:
            execute_query(
                """INSERT INTO payment_promo_codes (razorpay_order_id, promo_code, user_id, created_at)
                VALUES (%s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE promo_code = %s""",
                (order["id"], body.promo_code.strip().upper(), current_user["id"], body.promo_code.strip().upper())
            )

        logger.info(f"✅ Razorpay order created: {order['id']} for user {current_user['id']}" + 
                     (f" with promo: {body.promo_code}" if body.promo_code else ""))
        return {
            "success": True,
            "order_id": order["id"],
            "amount": body.amount,
            "amount_paise": int(body.amount * 100),
            "currency": "INR",
            "merchant_transaction_id": merchant_txn_id
        }
    except Exception as e:
        logger.error(f"❌ Order creation failed: {e}")
        raise HTTPException(status_code=500, detail="Payment order creation failed")


# ── POST verify-payment (Razorpay Step 2) ────────────────
@router.post("/verify-payment")
def verify_payment(
    body: VerifyPaymentRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        # 1. Verify Razorpay signature
        key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        msg = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
        generated_signature = hmac.new(
            key_secret.encode(),
            msg.encode(),
            hashlib.sha256
        ).hexdigest()

        if generated_signature != body.razorpay_signature:
            logger.warning(f"❌ Invalid signature for user {current_user['id']}")
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # 2. ✅ Idempotency — check if already credited
        if is_payment_already_credited(body.razorpay_order_id):
            wallet = ensure_wallet_exists(current_user["id"])
            return {
                "success": True,
                "message": "Payment already processed",
                "wallet": wallet,
                "bonus": None
            }

        # 3. ✅ Also check transaction table (belt + suspenders)
        existing = execute_query(
            "SELECT id FROM transactions WHERE reference_id = %s",
            (body.razorpay_payment_id,),
            fetch_one=True
        )
        if existing:
            wallet = ensure_wallet_exists(current_user["id"])
            return {
                "success": True,
                "message": "Payment already processed",
                "wallet": wallet,
                "bonus": None
            }

        # 4. ✅ Mark payment as success in payments table
        mark_payment_success(
            razorpay_order_id=body.razorpay_order_id,
            razorpay_payment_id=body.razorpay_payment_id,
            razorpay_signature=body.razorpay_signature
        )

        # 5. ✅ Credit wallet using helper
        wallet = credit_wallet(current_user["id"], body.amount, update_total_added=True)

        # 6. ✅ Record transaction using helper
        record_add_money(
            user_id=current_user["id"],
            amount=body.amount,
            razorpay_payment_id=body.razorpay_payment_id,
            razorpay_order_id=body.razorpay_order_id
        )

        # 7. ✅ Get the deposit transaction ID
        txn = execute_query(
            "SELECT id FROM transactions WHERE user_id = %s AND type = 'add_money' ORDER BY id DESC LIMIT 1",
            (current_user["id"],), fetch_one=True
        )
        transaction_id = txn["id"] if txn else None

        # 8. ✅ Resolve promo code — from body, or from stored order
        promo_code = body.promo_code
        if not promo_code:
            stored_promo = execute_query(
                "SELECT promo_code FROM payment_promo_codes WHERE razorpay_order_id = %s AND user_id = %s",
                (body.razorpay_order_id, current_user["id"]), fetch_one=True
            )
            if stored_promo:
                promo_code = stored_promo["promo_code"]

        # 9. ✅ Check and apply bonus (SAFE — never breaks payment)
        bonus = None
        try:
            bonus = check_and_apply_bonus(
                user_id=current_user["id"],
                deposit_amount=body.amount,
                deposit_txn_id=transaction_id,
                promo_code=promo_code
            )
        except Exception as bonus_err:
            logger.error(f"⚠️ Bonus failed (payment OK): {bonus_err}")

        bonus_msg = ""
        if bonus:
            bonus_msg = f" + ₹{bonus['bonus_amount']} bonus!"
            logger.info(f"🎁 Bonus applied: {bonus['type']} — ₹{bonus['bonus_amount']} for user {current_user['id']}")

        logger.info(f"✅ Payment verified: User {current_user['id']} | ₹{body.amount}{bonus_msg}")
        return {
            "success": True,
            "message": f"₹{body.amount} added successfully!{bonus_msg}",
            "wallet": ensure_wallet_exists(current_user["id"]),
            "bonus": bonus
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Payment verification error: {e}")
        raise HTTPException(status_code=500, detail="Payment verification failed")


# ── POST deduct (⚠️ ADMIN-ONLY — internal use) ────────────────
@router.post("/deduct")
def deduct_money(
    body: DeductRequest,
    current_user: dict = Depends(get_current_user)
):
    # 🔴 FIX: Only admin can manually deduct. Calls/chat use helpers directly.
    if current_user.get("user_type") != "admin":
        raise HTTPException(status_code=403, detail="This endpoint is restricted")

    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

    allowed_types = ["chat", "call", "audio_call", "video_call", "content"]
    if body.type not in allowed_types:
        raise HTTPException(status_code=400, detail="Invalid transaction type")

    # ✅ Use helper — checks balance, updates total_spent
    wallet = debit_wallet(current_user["id"], body.amount)
    if wallet is None:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    # ✅ Record transaction (basic — call/chat endpoints use their own helpers)
    from app.helpers.transaction_helper import record_transaction
    record_transaction(
        user_id=current_user["id"],
        txn_type=body.type,
        amount=body.amount,
        description=body.description,
        reference_id=body.reference_id
    )

    return {"success": True, "message": "Balance deducted successfully", "wallet": wallet}
