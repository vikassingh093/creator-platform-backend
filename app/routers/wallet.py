from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wallet", tags=["Wallet"])

class AddMoneyRequest(BaseModel):
    amount: float
    payment_id: str

class DeductRequest(BaseModel):
    amount: float
    description: str
    type: str = "chat"  # chat, call, content
    reference_id: str = None

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

@router.post("/add-money")
def add_money(
    body: AddMoneyRequest,
    current_user: dict = Depends(get_current_user)
):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")
    if body.amount < 10:
        raise HTTPException(status_code=400, detail="Minimum recharge is ₹10")
    if body.amount > 10000:
        raise HTTPException(status_code=400, detail="Maximum recharge is ₹10,000")

    # Check duplicate payment
    existing = execute_query(
        "SELECT id FROM transactions WHERE reference_id = %s",
        (body.payment_id,),
        fetch_one=True
    )
    if existing:
        raise HTTPException(status_code=400, detail="Payment already processed")

    # Add balance
    execute_query(
        "UPDATE wallets SET balance = balance + %s WHERE user_id = %s",
        (body.amount, current_user["id"])
    )

    # Record transaction with correct type
    execute_query(
        """
        INSERT INTO transactions (user_id, type, amount, description, reference_id, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (current_user["id"], "add_money", body.amount, "Wallet recharge via Razorpay", body.payment_id, "success")
    )

    wallet = execute_query(
        "SELECT * FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )

    logger.info(f"✅ Wallet credited: User {current_user['id']} | Amount ₹{body.amount}")

    return {
        "success": True,
        "message": f"₹{body.amount} added successfully!",
        "wallet": wallet
    }

@router.post("/deduct")
def deduct_money(
    body: DeductRequest,
    current_user: dict = Depends(get_current_user)
):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

    # Validate type
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

    # Deduct balance
    execute_query(
        "UPDATE wallets SET balance = balance - %s WHERE user_id = %s",
        (body.amount, current_user["id"])
    )

    # Record transaction
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

    return {
        "success": True,
        "message": "Balance deducted successfully",
        "wallet": wallet
    }