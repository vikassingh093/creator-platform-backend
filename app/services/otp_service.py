import random
import string
import requests
from app.config import (
    OTP_EXPIRE_MINUTES,
    OTP_LENGTH,
    MTALKZ_API_KEY,
    MTALKZ_SENDER_ID,
    MTALKZ_FORMAT,
    MTALKZ_SEND_SMS_URL
)
from app.redis_client import redis_set, redis_get, redis_delete, redis_increment
import logging

logger = logging.getLogger(__name__)

OTP_PREFIX          = "otp:"
MAX_OTP_ATTEMPTS    = 5

TEST_PHONES = [
    "9999999999", "9876543210", "9111111111", "9222222222",
    "9333333333", "9444444444", "9555555555", "9666666666",
    "9777777777", "9888888888", "9000000001", "9000000002",
    "0000000000"
]
TEST_OTP = "123456"


def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=OTP_LENGTH))


def send_sms_mtalkz(phone: str, otp: str) -> bool:
    """Send real OTP via mTalkz with 30 sec timeout"""
    try:
        # ✅ mTalkz requires 91 prefix for Indian numbers
        formatted_phone = phone if phone.startswith("91") else f"91{phone}"

        message = f"Your OTP is {otp}. Valid for {OTP_EXPIRE_MINUTES} minutes. Do not share with anyone."

        # ✅ Build full URL with query params (primary mTalkz method)
        full_url = (
            f"{MTALKZ_SEND_SMS_URL}"
            f"?apikey={MTALKZ_API_KEY}"
            f"&senderid={MTALKZ_SENDER_ID}"
            f"&number={formatted_phone}"
            f"&message={message}"
            f"&format={MTALKZ_FORMAT}"
        )

        logger.info(f"📤 Sending OTP to {formatted_phone} via mTalkz...")
        logger.info(f"🔑 DEBUG: API_KEY=[{MTALKZ_API_KEY}], SENDER=[{MTALKZ_SENDER_ID}], URL=[{MTALKZ_SEND_SMS_URL}]")

        response = requests.get(
            full_url,
            timeout=30
        )

        logger.info(f"📥 mTalkz status code: {response.status_code}")
        logger.info(f"📥 mTalkz raw response: {response.text}")

        # ── Parse response ─────────────────────────────────
        try:
            data = response.json()
            logger.info(f"📥 mTalkz parsed response: {data}")
        except Exception:
            logger.warning(f"⚠️ mTalkz response is not JSON: {response.text}")
            data = {}

        # ── Validate HTTP status ───────────────────────────
        if response.status_code != 200:
            logger.error(f"❌ mTalkz HTTP error {response.status_code}: {data}")
            return False

        # ── Validate mTalkz response body ──────────────────
        mtalkz_status = str(data.get("status", "")).upper()

        if mtalkz_status == "OK":
            logger.info(f"✅ OTP successfully sent to {formatted_phone} via mTalkz")
            return True

        # Any other status is an error
        logger.error(f"❌ mTalkz API error: status={mtalkz_status}, message={data.get('message')}")
        return False

    except requests.exceptions.Timeout:
        logger.error(f"❌ mTalkz TIMEOUT — no response within 30 seconds for {phone}")
        return False
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ mTalkz CONNECTION ERROR for {phone}: {e}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ mTalkz REQUEST ERROR for {phone}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ mTalkz UNEXPECTED ERROR for {phone}: {e}", exc_info=True)
        return False


def send_otp(phone: str) -> dict:
    logger.info(f"📞 send_otp called for phone: {phone}")

    # ── Test phones — skip real SMS ────────────────────────
    if phone in TEST_PHONES:
        logger.info(f"🧪 [TEST MODE] Phone {phone} — skipping mTalkz, OTP={TEST_OTP}")
        redis_set(
            f"{OTP_PREFIX}{phone}",
            {"otp": TEST_OTP, "verified": False},
            expire_seconds=OTP_EXPIRE_MINUTES * 60
        )
        return {
            "success": True,
            "message": "OTP sent successfully",
            "mock_otp": TEST_OTP,
            "expires_in": OTP_EXPIRE_MINUTES * 60
        }

    # ── Rate limit ─────────────────────────────────────────
    rate_key = f"otp_rate:{phone}"
    count = redis_increment(rate_key, expire_seconds=3600)
    logger.info(f"🔢 OTP request count for {phone}: {count}/5")
    if count > 5:
        logger.warning(f"🚫 Rate limit hit for {phone}")
        return {
            "success": False,
            "message": "Too many OTP requests. Try after 1 hour."
        }

    # ── Generate OTP ───────────────────────────────────────
    otp = generate_otp()
    expire_seconds = OTP_EXPIRE_MINUTES * 60

    # ── Send real SMS FIRST ────────────────────────────────
    sent = send_sms_mtalkz(phone, otp)
    if not sent:
        logger.error(f"❌ Failed to send OTP to {phone} via mTalkz")
        # ✅ Don't store OTP in Redis if SMS failed
        redis_delete(f"{OTP_PREFIX}{phone}")
        return {
            "success": False,
            "message": "Failed to send OTP. Please try again."
        }

    # ✅ Store OTP in Redis ONLY after SMS sent successfully
    redis_set(
        f"{OTP_PREFIX}{phone}",
        {"otp": otp, "verified": False},
        expire_seconds=expire_seconds
    )
    logger.info(f"💾 OTP stored in Redis for {phone} — expires in {expire_seconds}s")

    logger.info(f"✅ OTP flow complete for {phone}")
    return {
        "success": True,
        "message": "OTP sent successfully",
        "expires_in": expire_seconds
    }


def verify_otp(phone: str, otp: str) -> dict:
    logger.info(f"🔍 verify_otp called for phone: {phone}")

    # ── Test phones ────────────────────────────────────────
    if phone in TEST_PHONES:
        if otp == TEST_OTP:
            logger.info(f"✅ [TEST MODE] OTP verified for {phone}")
            return {"success": True, "message": "OTP verified"}
        else:
            logger.warning(f"❌ [TEST MODE] Wrong OTP for {phone}: got {otp}, expected {TEST_OTP}")
            return {"success": False, "message": "Invalid OTP"}

    stored = redis_get(f"{OTP_PREFIX}{phone}")
    if not stored:
        logger.warning(f"⚠️ No OTP found in Redis for {phone} — expired or not sent")
        return {"success": False, "message": "OTP expired. Please request a new one."}

    if stored["otp"] != otp:
        logger.warning(f"❌ OTP mismatch for {phone}: got {otp}, expected {stored['otp']}")
        return {"success": False, "message": "Invalid OTP"}

    if stored.get("verified"):
        logger.warning(f"⚠️ OTP already used for {phone}")
        return {"success": False, "message": "OTP already used"}

    redis_delete(f"{OTP_PREFIX}{phone}")
    logger.info(f"✅ OTP verified and deleted from Redis for {phone}")
    return {"success": True, "message": "OTP verified successfully"}