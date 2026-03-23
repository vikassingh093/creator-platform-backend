from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "CreatorHub")
    APP_ENV: str = os.getenv("APP_ENV", "local")
    APP_PORT: int = int(os.getenv("APP_PORT", 8000))

    SECRET_KEY: str = os.getenv("SECRET_KEY", "changeme")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 30))

    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", 3306))
    DB_USER: str = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME: str = os.getenv("DB_NAME", "creator_platform")

    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", None)

    OTP_EXPIRE_MINUTES: int = int(os.getenv("OTP_EXPIRE_MINUTES", 5))
    OTP_LENGTH: int = int(os.getenv("OTP_LENGTH", 6))

    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads")
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", 50))

    PHONEPE_MERCHANT_ID: str = os.getenv("PHONEPE_MERCHANT_ID")
    PHONEPE_SALT_KEY: str = os.getenv("PHONEPE_SALT_KEY")
    PHONEPE_SALT_INDEX: int = int(os.getenv("PHONEPE_SALT_INDEX", 1))
    PHONEPE_BASE_URL: str = os.getenv("PHONEPE_BASE_URL")

    WORKERS: int = int(os.getenv("WORKERS", 1))

settings = Settings()