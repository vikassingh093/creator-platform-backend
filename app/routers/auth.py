from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.otp_service import send_otp, verify_otp
from app.services.jwt_service import create_access_token, create_refresh_token, revoke_token, verify_token
from app.database import execute_query
from app.redis_client import redis_set, redis_delete
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

SESSION_EXPIRE = 60 * 60 * 2  # 2 hours

class SendOTPRequest(BaseModel):
    phone: str
    name: str = None
    user_type: str = "user"

class VerifyOTPRequest(BaseModel):
    phone: str
    otp: str

@router.post("/send-otp")
def send_otp_route(body: SendOTPRequest):
    # Normalize phone - remove +91 or 91 prefix if present
    phone = body.phone.strip()
    phone = phone.replace("+91", "").replace(" ", "")
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    logger.info(f"📲 /send-otp endpoint hit — normalized phone: {phone}")
    result = send_otp(phone)
    logger.info(f"📲 /send-otp result: {result}")
    return result

@router.post("/verify-otp")
def verify_otp_route(body: VerifyOTPRequest):
    phone = body.phone.strip()
    phone = phone.replace("+91", "").replace(" ", "")
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    result = verify_otp(phone, body.otp)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    # Get or create user
    user = execute_query(
        "SELECT * FROM users WHERE phone = %s",
        (phone,),
        fetch_one=True
    )
    if not user:
        execute_query(
            "INSERT INTO users (name, phone, user_type, is_active, is_blocked) VALUES (%s, %s, 'user', 1, 0)",
            (f"User_{phone[-4:]}", phone)
        )
        user = execute_query(
            "SELECT * FROM users WHERE phone = %s",
            (phone,),
            fetch_one=True
        )

    if user["is_blocked"]:
        raise HTTPException(status_code=403, detail="Account is blocked")

    token_data = {
        "sub": str(user["id"]),
        "user_type": user["user_type"],
        "phone": user["phone"]
    }

    access_token = create_access_token(token_data, user["user_type"])
    refresh_token = create_refresh_token(token_data, user["user_type"])

    return {
        "success": True,
        "message": "Login successful!",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "phone": user["phone"],
            "email": user.get("email"),
            "profile_photo": user.get("profile_photo"),
            "user_type": user["user_type"],
        }
    }

@router.post("/logout")
def logout(body: dict):
    user_id = body.get("user_id")
    if user_id:
        redis_delete(f"session:{user_id}")
        revoke_token(user_id)
        logger.info(f"✅ User logged out: ID {user_id}")
    return {"success": True, "message": "Logged out successfully"}

@router.post("/refresh")
def refresh_token(data: dict):
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required")

    payload = verify_token(refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user_id = payload.get("sub") or payload.get("user_id") or payload.get("id")
    user = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (int(user_id),),
        fetch_one=True
    )
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_token = create_access_token({"sub": str(user["id"]), "user_type": user["user_type"]})
    return {"access_token": new_token, "token_type": "bearer"}