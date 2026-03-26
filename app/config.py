import os
from pathlib import Path

# ✅ Load .env manually — no dotenv library needed
# Reads .env once at server start, loads all values into os.environ
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            os.environ.setdefault(key, value)

# ─── APP ──────────────────────────────────────────────────
APP_NAME    = os.getenv("APP_NAME", "CreatorHub")
APP_ENV     = os.getenv("APP_ENV", "local")
APP_PORT    = int(os.getenv("APP_PORT", 8000))

# ─── JWT ──────────────────────────────────────────────────
SECRET_KEY                    = os.getenv("SECRET_KEY", "changeme")
ALGORITHM                     = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES   = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
REFRESH_TOKEN_EXPIRE_DAYS     = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 30))

# ─── DATABASE ─────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "creator_platform")

# ─── REDIS ────────────────────────────────────────────────
REDIS_HOST      = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT      = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB        = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD  = os.getenv("REDIS_PASSWORD", None)

# ─── OTP ──────────────────────────────────────────────────
OTP_EXPIRE_MINUTES  = int(os.getenv("OTP_EXPIRE_MINUTES", 5))
OTP_LENGTH          = int(os.getenv("OTP_LENGTH", 6))

# ─── UPLOADS ──────────────────────────────────────────────
UPLOAD_DIR      = os.getenv("UPLOAD_DIR", "uploads")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", 50))

# ─── PHONEPE ──────────────────────────────────────────────
PHONEPE_MERCHANT_ID = os.getenv("PHONEPE_MERCHANT_ID")
PHONEPE_SALT_KEY    = os.getenv("PHONEPE_SALT_KEY")
PHONEPE_SALT_INDEX  = int(os.getenv("PHONEPE_SALT_INDEX", 1))
PHONEPE_BASE_URL    = os.getenv("PHONEPE_BASE_URL")

# ─── SERVER ───────────────────────────────────────────────
WORKERS = int(os.getenv("WORKERS", 1))

# ─── CALL RATES ───────────────────────────────────────────
AUDIO_RATE    = 20    # ₹20/min
VIDEO_RATE    = 50    # ₹50/min
TICK_INTERVAL = 5     # deduct every 5 seconds

# ─── COMMISSION ───────────────────────────────────────────
CALL_CREATOR_COMMISSION  = 0.50   # 50% of call cost goes to creator
CHAT_PLATFORM_COMMISSION = 50.0   # platform takes 50% of chat cost

# ─── CALL SETTINGS ────────────────────────────────────────
CALL_RING_TIMEOUT_SECONDS  = 30   # auto-expire ringing calls after 30s
CALL_STUCK_TIMEOUT_MINUTES = 5    # auto-expire stuck active calls after 5min

# ─── MTALKZ ───────────────────────────────────────────────
MTALKZ_API_KEY      = os.getenv("MTALKZ_API_KEY", "")
MTALKZ_SENDER_ID    = os.getenv("MTALKZ_SENDER_ID", "")
MTALKZ_FORMAT       = os.getenv("MTALKZ_FORMAT", "json")
MTALKZ_SEND_SMS_URL = os.getenv("MTALKZ_SEND_SMS_BASE_URL", "https://msgn.mtalkz.com/api")