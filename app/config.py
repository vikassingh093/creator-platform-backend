import os
import logging
import sys
from pathlib import Path

# ✅ Load .env manually — no dotenv library needed
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
APP_NAME = os.getenv("APP_NAME", "CreatorHub")
APP_ENV  = os.getenv("APP_ENV", "local")  # "local" or "production"
APP_PORT = int(os.getenv("APP_PORT", 8000))
DEBUG    = APP_ENV == "local"  # True on local, False on production

# ─── JWT ──────────────────────────────────────────────────
SECRET_KEY                  = os.getenv("SECRET_KEY", "changeme")
ALGORITHM                   = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
REFRESH_TOKEN_EXPIRE_DAYS   = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 30))

# ─── DATABASE ─────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "creator_platform")

# ─── DB POOL (different for local vs production) ──────────
if DEBUG:
    # Local: small pool, enough for development
    DB_POOL_SIZE     = int(os.getenv("DB_POOL_SIZE", 5))
    DB_MAX_OVERFLOW  = int(os.getenv("DB_MAX_OVERFLOW", 5))
    DB_POOL_TIMEOUT  = int(os.getenv("DB_POOL_TIMEOUT", 10))
    DB_POOL_RECYCLE  = int(os.getenv("DB_POOL_RECYCLE", 3600))
    DB_ECHO_SQL      = True  # Log all SQL queries in local
else:
    # Production: large pool for 1L+ requests/day
    DB_POOL_SIZE     = int(os.getenv("DB_POOL_SIZE", 20))
    DB_MAX_OVERFLOW  = int(os.getenv("DB_MAX_OVERFLOW", 30))
    DB_POOL_TIMEOUT  = int(os.getenv("DB_POOL_TIMEOUT", 10))
    DB_POOL_RECYCLE  = int(os.getenv("DB_POOL_RECYCLE", 1800))
    DB_ECHO_SQL      = False

# ─── REDIS ────────────────────────────────────────────────
REDIS_HOST     = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT     = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB       = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# ─── OTP ──────────────────────────────────────────────────
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", 5))
OTP_LENGTH         = int(os.getenv("OTP_LENGTH", 6))

# ─── UPLOADS ──────────────────────────────────────────────
UPLOAD_DIR       = os.getenv("UPLOAD_DIR", "uploads")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", 50))

# ─── PHONEPE ──────────────────────────────────────────────
PHONEPE_MERCHANT_ID = os.getenv("PHONEPE_MERCHANT_ID")
PHONEPE_SALT_KEY    = os.getenv("PHONEPE_SALT_KEY")
PHONEPE_SALT_INDEX  = int(os.getenv("PHONEPE_SALT_INDEX", 1))
PHONEPE_BASE_URL    = os.getenv("PHONEPE_BASE_URL")

# ─── SERVER ───────────────────────────────────────────────
WORKERS = int(os.getenv("WORKERS", 1 if DEBUG else 2))

# ─── CALL RATES ───────────────────────────────────────────
AUDIO_RATE    = 20
VIDEO_RATE    = 50
TICK_INTERVAL = 5

# ─── COMMISSION ───────────────────────────────────────────
CALL_CREATOR_COMMISSION  = 0.50
CHAT_PLATFORM_COMMISSION = 50.0

# ─── CALL SETTINGS ────────────────────────────────────────
CALL_RING_TIMEOUT_SECONDS  = 30
CALL_STUCK_TIMEOUT_MINUTES = 5

# ─── MTALKZ ───────────────────────────────────────────────
MTALKZ_API_KEY      = os.getenv("MTALKZ_API_KEY", "")
MTALKZ_SENDER_ID    = os.getenv("MTALKZ_SENDER_ID", "")
MTALKZ_FORMAT       = os.getenv("MTALKZ_FORMAT", "json")
MTALKZ_SEND_SMS_URL = os.getenv("MTALKZ_SEND_SMS_BASE_URL", "https://msgn.mtalkz.com/api")

# ─── SLOW QUERY THRESHOLD ────────────────────────────────
SLOW_QUERY_THRESHOLD = float(os.getenv("SLOW_QUERY_THRESHOLD", 0.5 if not DEBUG else 1.0))

# ═══════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════

def setup_logging():
    """
    LOCAL:      DEBUG level, console output, verbose format
    PRODUCTION: INFO level, console + file output, JSON-like format
    """

    log_level = logging.DEBUG if DEBUG else logging.INFO

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # ── Console handler (always) ──────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    if DEBUG:
        # Local: readable, colored-ish format
        console_fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S"
        )
    else:
        # Production: structured format for log analysis
        console_fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # ── File handler (production only) ────────────────────
    if not DEBUG:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,              # Keep 5 rotated files
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)

    # ── Suppress noisy libraries ──────────────────────────
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING if not DEBUG else logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.INFO if not DEBUG else logging.DEBUG)

    # ── Startup banner ────────────────────────────────────
    logger = logging.getLogger("app.config")
    logger.info("=" * 60)
    logger.info(f"🚀 {APP_NAME} starting in {'LOCAL' if DEBUG else 'PRODUCTION'} mode")
    logger.info(f"   ENV={APP_ENV} | DEBUG={DEBUG} | WORKERS={WORKERS}")
    logger.info(f"   DB={DB_HOST}:{DB_PORT}/{DB_NAME} | Pool={DB_POOL_SIZE}+{DB_MAX_OVERFLOW}")
    logger.info(f"   Log level: {logging.getLevelName(log_level)}")
    logger.info("=" * 60)


# Need this import for RotatingFileHandler
import logging.handlers

# Auto-setup logging when config is imported
setup_logging()