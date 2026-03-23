import random
import string
from app.config import settings
from app.redis_client import redis_set, redis_get, redis_delete, redis_increment
import logging

logger = logging.getLogger(__name__)

OTP_PREFIX = "otp:"
OTP_ATTEMPTS_PREFIX = "otp_attempts:"
MAX_OTP_ATTEMPTS = 5

TEST_PHONES = [
    "9999999999", "9876543210", "9111111111", "9222222222",
    "9333333333", "9444444444", "9555555555", "9666666666",
    "9777777777", "9888888888", "9000000001", "9000000002",
    "0000000000"
]
TEST_OTP = "123456"

def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=settings.OTP_LENGTH))

def send_otp(phone: str) -> dict:
    logger.info(f"send_otp called with phone: '{phone}'")
    logger.info(f"TEST_PHONES list: {TEST_PHONES}")
    logger.info(f"Is test phone: {phone in TEST_PHONES}")

    if phone in TEST_PHONES:
        logger.info(f"[TEST MODE] Phone: {phone} | OTP: {TEST_OTP}")
        return {
            "success": True,
            "message": "OTP sent successfully",
            "mock_otp": TEST_OTP,
            "expires_in": 300
        }

    rate_key = f"otp_rate:{phone}"
    count = redis_increment(rate_key, expire_seconds=3600)
    if count > 5:
        return {
            "success": False,
            "message": "Too many OTP requests. Try after 1 hour."
        }

    otp = generate_otp()
    expire_seconds = settings.OTP_EXPIRE_MINUTES * 60

    redis_set(
        f"{OTP_PREFIX}{phone}",
        {"otp": otp, "verified": False},
        expire_seconds=expire_seconds
    )

    logger.info(f"[MOCK OTP] Phone: {phone} | OTP: {otp}")

    return {
        "success": True,
        "message": "OTP sent successfully",
        "mock_otp": otp,
        "expires_in": expire_seconds
    }

def verify_otp(phone: str, otp: str) -> dict:
    # Test phones - bypass real OTP verification
    if phone in TEST_PHONES:
        if otp == TEST_OTP:
            logger.info(f"[TEST MODE] OTP verified for {phone}")
            return {
                "success": True,
                "message": "OTP verified successfully!"
            }
        else:
            return {
                "success": False,
                "message": "Invalid OTP."
            }

    attempts_key = f"{OTP_ATTEMPTS_PREFIX}{phone}"
    attempts = redis_increment(attempts_key, expire_seconds=600)
    if attempts > MAX_OTP_ATTEMPTS:
        return {
            "success": False,
            "message": "Too many wrong attempts. Request a new OTP."
        }

    stored = redis_get(f"{OTP_PREFIX}{phone}")
    if not stored:
        return {
            "success": False,
            "message": "OTP expired or not found. Please request again."
        }

    if stored["otp"] != otp:
        return {
            "success": False,
            "message": f"Invalid OTP. {MAX_OTP_ATTEMPTS - attempts} attempts left."
        }

    redis_delete(f"{OTP_PREFIX}{phone}")
    redis_delete(attempts_key)

    return {
        "success": True,
        "message": "OTP verified successfully!"
    }

def send_otp_test(phone: str):
    if phone in TEST_PHONES:
        return {"success": True, "message": "OTP sent (test mode)"}
    return send_otp(phone)

def verify_otp_test(phone: str, otp: str):
    if phone in TEST_PHONES and otp == TEST_OTP:
        return {"success": True, "message": "OTP verified"}
    return verify_otp(phone, otp)