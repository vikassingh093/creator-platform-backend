from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.services.otp_service import send_otp, verify_otp
from app.services.jwt_service import create_access_token, create_refresh_token, revoke_token, verify_token
from app.database import execute_query
from app.redis_client import redis_set, redis_get, redis_delete, redis_increment, redis_expire
from app.middleware.auth_middleware import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Rate limit settings ───────────────────────────────────
OTP_SEND_LIMIT = 3          # Max 3 OTPs per phone per window
OTP_SEND_WINDOW = 600       # 10 minute window
OTP_VERIFY_LIMIT = 5        # Max 5 verify attempts per phone
OTP_VERIFY_WINDOW = 600     # 10 minute window
OTP_LOCKOUT_TIME = 1800     # 30 min lockout after too many failed attempts


class SendOTPRequest(BaseModel):
    phone: str
    name: str = None
    user_type: str = "user"

class VerifyOTPRequest(BaseModel):
    phone: str
    otp: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str


@router.post("/send-otp")
def send_otp_route(body: SendOTPRequest):
    phone = body.phone.strip()
    phone = phone.replace("+91", "").replace(" ", "")
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    # 🔴 FIX #4: Rate limit — max 3 OTPs per phone per 10 min
    lockout_key = f"otp_lockout:{phone}"
    if redis_get(lockout_key):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait 30 minutes."
        )

    rate_key = f"otp_send:{phone}"
    count = redis_increment(rate_key, expire_seconds=OTP_SEND_WINDOW)
    if count > OTP_SEND_LIMIT:
        logger.warning(f"🚫 OTP send rate limited: {phone} ({count} attempts)")
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests. Please wait 10 minutes."
        )

    logger.info(f"📲 /send-otp — phone: {phone} (attempt {count}/{OTP_SEND_LIMIT})")
    result = send_otp(phone)
    return result


@router.post("/verify-otp")
def verify_otp_route(body: VerifyOTPRequest):
    phone = body.phone.strip()
    phone = phone.replace("+91", "").replace(" ", "")
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    # 🔴 FIX #5: Rate limit — max 5 verify attempts per 10 min
    lockout_key = f"otp_lockout:{phone}"
    if redis_get(lockout_key):
        raise HTTPException(
            status_code=429,
            detail="Account temporarily locked. Try again in 30 minutes."
        )

    verify_key = f"otp_verify:{phone}"
    count = redis_increment(verify_key, expire_seconds=OTP_VERIFY_WINDOW)

    if count > OTP_VERIFY_LIMIT:
        # Lock the phone for 30 minutes
        redis_set(lockout_key, "locked", OTP_LOCKOUT_TIME)
        logger.warning(f"🔒 Phone locked due to too many OTP attempts: {phone}")
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Account locked for 30 minutes."
        )

    result = verify_otp(phone, body.otp)
    if not result["success"]:
        remaining = OTP_VERIFY_LIMIT - count
        logger.info(f"❌ OTP verify failed: {phone} (attempt {count}, {remaining} left)")
        raise HTTPException(status_code=400, detail=result["message"])

    # ✅ OTP verified — clear rate limit counters
    redis_delete(verify_key)
    redis_delete(f"otp_send:{phone}")

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
            "avatar_id": user.get("avatar_id"),
        }
    }


# 🔴 FIX #3: Logout now requires authentication — can only log out yourself
@router.post("/logout")
def logout(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    redis_delete(f"session:{user_id}")
    revoke_token(user_id)
    logger.info(f"✅ User logged out: ID {user_id}")
    return {"success": True, "message": "Logged out successfully"}


# ✅ FIX: Use Pydantic model instead of raw dict
@router.post("/refresh")
def refresh_token_route(body: RefreshTokenRequest):
    if not body.refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required")

    payload = verify_token(body.refresh_token)
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