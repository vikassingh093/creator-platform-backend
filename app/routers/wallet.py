from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
import logging
import os
import hmac
import hashlib

# ✅ FIXED: lazy import so server won't crash if razorpay missing
try:
    import razorpay
except ImportError:
    razorpay = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wallet", tags=["Wallet"])

class AddMoneyRequest(BaseModel):
    amount: float
    payment_id: str

class DeductRequest(BaseModel):
    amount: float
    description: str
    type: str = "chat"
    reference_id: str = None

# ── NEW: Razorpay Models ──────────────────────────────────
class CreateOrderRequest(BaseModel):
    amount: float

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    amount: float

@router.get("/")
def get_wallet(current_user: dict = Depends(get_current_user)):
    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not wallet:
        execute_query(
            "INSERT INTO wallets (user_id, balance) VALUES (%s, %s)",
            (current_user["id"], 0)
        )
        wallet = {"user_id": current_user["id"], "balance": 0}
    return {"success": True, "wallet": wallet}

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

# ── KEPT: existing add-money (untouched) ─────────────────
@router.post("/add-money")
def add_money(
    body: AddMoneyRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        if body.amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid amount")
        if body.amount < 10:
            raise HTTPException(status_code=400, detail="Minimum recharge is ₹10")
        if body.amount > 10000:
            raise HTTPException(status_code=400, detail="Maximum recharge is ₹10,000")

        existing = execute_query(
            "SELECT id FROM transactions WHERE reference_id = %s",
            (body.payment_id,),
            fetch_one=True
        )
        if existing:
            raise HTTPException(status_code=400, detail="Payment already processed")

        wallet = execute_query(
            "SELECT * FROM wallets WHERE user_id = %s",
            (current_user["id"],),
            fetch_one=True
        )
        if not wallet:
            execute_query(
                "INSERT INTO wallets (user_id, balance, total_added, total_spent) VALUES (%s, 0, 0, 0)",
                (current_user["id"],)
            )

        execute_query(
            "UPDATE wallets SET balance = balance + %s, total_added = total_added + %s WHERE user_id = %s",
            (body.amount, body.amount, current_user["id"])
        )
        execute_query(
            """
            INSERT INTO transactions (user_id, type, amount, description, reference_id, status)
            VALUES (%s, 'add_money', %s, %s, %s, 'success')
            """,
            (current_user["id"], body.amount, f"Wallet recharge ₹{body.amount}", body.payment_id)
        )

        wallet = execute_query(
            "SELECT * FROM wallets WHERE user_id = %s",
            (current_user["id"],),
            fetch_one=True
        )
        logger.info(f"✅ Wallet credited: User {current_user['id']} | Amount ₹{body.amount}")
        return {"success": True, "message": f"₹{body.amount} added successfully!", "wallet": wallet}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Add money error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

# ── NEW: Razorpay Step 1 - Create Order ──────────────────
@router.post("/create-order")
def create_order(
    body: CreateOrderRequest,
    current_user: dict = Depends(get_current_user)
):
    # ✅ FIXED: check if razorpay is available
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
        order = client.order.create({
            "amount": int(body.amount * 100),  # convert to paise
            "currency": "INR",
            "receipt": f"user_{current_user['id']}",
            "notes": {
                "user_id": str(current_user["id"]),
                "user_name": current_user.get("name", "")
            }
        })
        logger.info(f"✅ Razorpay order created: {order['id']} for user {current_user['id']}")
        return {
            "success": True,
            "order_id": order["id"],
            "amount": body.amount,
            "amount_paise": int(body.amount * 100),
            "currency": "INR"
        }
    except Exception as e:
        logger.error(f"❌ Order creation failed: {e}")
        raise HTTPException(status_code=500, detail="Payment order creation failed")

# ── NEW: Razorpay Step 2 - Verify Payment & Credit Wallet ─
@router.post("/verify-payment")
def verify_payment(
    body: VerifyPaymentRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        msg = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"

        # ✅ FIXED: hmac.new → hmac.new (correct Python function)
        generated_signature = hmac.new(
            key_secret.encode(),
            msg.encode(),
            hashlib.sha256
        ).hexdigest()

        if generated_signature != body.razorpay_signature:
            logger.warning(f"❌ Invalid signature for user {current_user['id']}")
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # 2. Check duplicate payment
        existing = execute_query(
            "SELECT id FROM transactions WHERE reference_id = %s",
            (body.razorpay_payment_id,),
            fetch_one=True
        )
        if existing:
            raise HTTPException(status_code=400, detail="Payment already processed")

        # 3. Ensure wallet exists
        wallet = execute_query(
            "SELECT * FROM wallets WHERE user_id = %s",
            (current_user["id"],),
            fetch_one=True
        )
        if not wallet:
            execute_query(
                "INSERT INTO wallets (user_id, balance, total_added, total_spent) VALUES (%s, 0, 0, 0)",
                (current_user["id"],)
            )

        # 4. Credit wallet
        execute_query(
            "UPDATE wallets SET balance = balance + %s, total_added = total_added + %s WHERE user_id = %s",
            (body.amount, body.amount, current_user["id"])
        )

        # 5. Record transaction
        execute_query(
            """
            INSERT INTO transactions (user_id, type, amount, description, reference_id, status)
            VALUES (%s, 'add_money', %s, %s, %s, 'success')
            """,
            (current_user["id"], body.amount, f"Wallet recharge ₹{body.amount}", body.razorpay_payment_id)
        )

        # 6. Return updated wallet
        updated_wallet = execute_query(
            "SELECT * FROM wallets WHERE user_id = %s",
            (current_user["id"],),
            fetch_one=True
        )
        logger.info(f"✅ Payment verified: User {current_user['id']} | ₹{body.amount}")
        return {
            "success": True,
            "message": f"₹{body.amount} added successfully!",
            "wallet": updated_wallet
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Payment verification error: {e}")
        raise HTTPException(status_code=500, detail="Payment verification failed")

# ── KEPT: existing deduct (untouched) ────────────────────
@router.post("/deduct")
def deduct_money(
    body: DeductRequest,
    current_user: dict = Depends(get_current_user)
):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

    allowed_types = ["chat", "call", "content"]
    if body.type not in allowed_types:
        raise HTTPException(status_code=400, detail="Invalid transaction type")

    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not wallet or float(wallet["balance"]) < body.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    execute_query(
        "UPDATE wallets SET balance = balance - %s WHERE user_id = %s",
        (body.amount, current_user["id"])
    )
    execute_query(
        """
        INSERT INTO transactions (user_id, type, amount, description, reference_id, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (current_user["id"], body.type, body.amount, body.description, body.reference_id, "success")
    )
    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    return {"success": True, "message": "Balance deducted successfully", "wallet": wallet}
