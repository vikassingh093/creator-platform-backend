from jose import JWTError, jwt
from datetime import datetime, timedelta
from app.config import settings
from app.redis_client import redis_set, redis_get, redis_delete
import logging

logger = logging.getLogger(__name__)

def create_access_token(data: dict, user_type: str = "user") -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "user_type": user_type})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict, user_type: str = "user") -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "user_type": user_type})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as e:
        logger.error(f"JWT Error: {e}")
        return None

def revoke_token(user_id: int):
    redis_delete(f"refresh_token:{user_id}")