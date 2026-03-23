from app.config import settings
from app.database import execute_query
import uuid
import hashlib
import base64
import json
import httpx
import logging

logger = logging.getLogger(__name__)

def generate_merchant_transaction_id() -> str:
    return f"MT_{uuid.uuid4().hex[:20].upper()}"

def initiate_payment(user_id: int, amount: float, phone: str) -> dict:
    """
    Initiate PhonePe payment
    MOCK mode: return fake payment URL for testing
    TODO: Replace with real PhonePe API in production
    """
    merchant_transaction_id = generate_merchant_transaction_id()
    amount_paise = int(amount * 100)  # PhonePe uses paise

    # Save pending payment
    execute_query(
        """
        INSERT INTO payments (user_id, merchant_transaction_id, amount, status)
        VALUES (%s, %s, %s, 'pending')
        """,
        (user_id, merchant_transaction_id, amount)
    )

    # MOCK response for sandbox/testing
    if settings.APP_ENV == "local":
        return {
            "merchant_transaction_id": merchant_transaction_id,
            "amount": amount,
            "payment_url": f"http://localhost:8000/api/v1/wallet/mock-payment-success?txn_id={merchant_transaction_id}&amount={amount}",
            "mode": "MOCK"
        }

    # Real PhonePe API
    payload = {
        "merchantId": settings.PHONEPE_MERCHANT_ID,
        "merchantTransactionId": merchant_transaction_id,
        "merchantUserId": f"USER_{user_id}",
        "amount": amount_paise,
        "redirectUrl": f"http://localhost:8000/api/v1/wallet/verify-payment",
        "redirectMode": "POST",
        "callbackUrl": f"http://localhost:8000/api/v1/wallet/phonepe-webhook",
        "mobileNumber": phone,
        "paymentInstrument": {"type": "PAY_PAGE"}
    }

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    checksum_str = encoded + "/pg/v1/pay" + settings.PHONEPE_SALT_KEY
    checksum = hashlib.sha256(checksum_str.encode()).hexdigest() + f"###{settings.PHONEPE_SALT_INDEX}"

    headers = {
        "Content-Type": "application/json",
        "X-VERIFY": checksum
    }

    try:
        response = httpx.post(
            f"{settings.PHONEPE_BASE_URL}/pg/v1/pay",
            json={"request": encoded},
            headers=headers,
            timeout=30
        )
        data = response.json()
        if data.get("success"):
            return {
                "merchant_transaction_id": merchant_transaction_id,
                "payment_url": data["data"]["instrumentResponse"]["redirectInfo"]["url"],
                "mode": "PHONEPE"
            }
    except Exception as e:
        logger.error(f"PhonePe error: {e}")

    raise Exception("Payment initiation failed")

def verify_payment(merchant_transaction_id: str) -> dict:
    """
    Verify PhonePe payment
    MOCK: auto success for local testing
    """
    payment = execute_query(
        "SELECT * FROM payments WHERE merchant_transaction_id = %s",
        (merchant_transaction_id,),
        fetch_one=True
    )
    if not payment:
        return {"success": False, "message": "Payment not found"}

    if payment["status"] == "success":
        return {"success": True, "amount": float(payment["amount"]), "message": "Already verified"}

    # MOCK: auto verify in local
    if settings.APP_ENV == "local":
        execute_query(
            "UPDATE payments SET status = 'success' WHERE merchant_transaction_id = %s",
            (merchant_transaction_id,)
        )
        return {
            "success": True,
            "amount": float(payment["amount"]),
            "message": "Payment verified (MOCK)"
        }

    # Real verification via PhonePe API
    checksum_str = f"/pg/v1/status/{settings.PHONEPE_MERCHANT_ID}/{merchant_transaction_id}{settings.PHONEPE_SALT_KEY}"
    checksum = hashlib.sha256(checksum_str.encode()).hexdigest() + f"###{settings.PHONEPE_SALT_INDEX}"

    try:
        response = httpx.get(
            f"{settings.PHONEPE_BASE_URL}/pg/v1/status/{settings.PHONEPE_MERCHANT_ID}/{merchant_transaction_id}",
            headers={"X-VERIFY": checksum, "X-MERCHANT-ID": settings.PHONEPE_MERCHANT_ID},
            timeout=30
        )
        data = response.json()
        if data.get("success") and data["data"]["state"] == "COMPLETED":
            execute_query(
                "UPDATE payments SET status = 'success', phonepe_transaction_id = %s WHERE merchant_transaction_id = %s",
                (data["data"]["transactionId"], merchant_transaction_id)
            )
            return {"success": True, "amount": float(payment["amount"]), "message": "Payment verified"}
    except Exception as e:
        logger.error(f"PhonePe verify error: {e}")

    return {"success": False, "message": "Payment verification failed"}