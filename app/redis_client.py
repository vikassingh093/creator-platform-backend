import redis
import json
from app.config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
import logging

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD if REDIS_PASSWORD else None,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
)

def redis_set(key: str, value, expire_seconds: int = None):
    try:
        data = json.dumps(value) if not isinstance(value, str) else value
        if expire_seconds:
            redis_client.setex(key, expire_seconds, data)
        else:
            redis_client.set(key, data)
        return True
    except Exception as e:
        logger.error(f"Redis SET error: {e}")
        return False

def redis_get(key: str):
    try:
        value = redis_client.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except:
            return value
    except Exception as e:
        logger.error(f"Redis GET error: {e}")
        return None

def redis_delete(key: str):
    try:
        redis_client.delete(key)
        return True
    except Exception as e:
        logger.error(f"Redis DELETE error: {e}")
        return False

def redis_exists(key: str) -> bool:
    try:
        return bool(redis_client.exists(key))
    except Exception as e:
        logger.error(f"Redis EXISTS error: {e}")
        return False

def redis_increment(key: str, expire_seconds: int = None) -> int:
    """Increment a key by 1. Sets expiry on first increment."""
    try:
        count = redis_client.incr(key)
        if expire_seconds and count == 1:
            redis_client.expire(key, expire_seconds)
        return count
    except Exception as e:
        logger.error(f"Redis INCR error: {e}")
        return 0

def redis_expire(key: str, seconds: int) -> bool:
    """Set expiry on an existing key."""
    try:
        return redis_client.expire(key, seconds)
    except Exception as e:
        logger.error(f"Redis EXPIRE error: {e}")
        return False

def redis_ttl(key: str) -> int:
    """Get remaining TTL of a key. Returns -1 if no expiry, -2 if key doesn't exist."""
    try:
        return redis_client.ttl(key)
    except Exception as e:
        logger.error(f"Redis TTL error: {e}")
        return -2