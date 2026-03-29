"""
Activity Service — Tracks customer online status via Redis.

How it works:
  - Every authenticated API request sets a Redis key: last_active:{user_id}
  - The key has a TTL of 120 seconds (auto-expires when customer stops browsing)
  - Creators can query online customers by scanning these keys
  - No heartbeat / no extra API calls from frontend needed

Redis keys used:
  - last_active:{user_id}  →  value: "1"  →  TTL: 120s
"""

import logging
from typing import List
from app.redis_client import redis_client, redis_set
from app.database import execute_query

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
ACTIVITY_TTL_SECONDS = 120       # Customer considered online for 2 minutes after last API call
ACTIVITY_KEY_PREFIX = "last_active:"  # Redis key pattern
MAX_ONLINE_CUSTOMERS = 50        # Max customers returned to creator


def update_user_activity(user_id: int) -> bool:
    """
    Mark a user as active in Redis.
    Called automatically by ActivityMiddleware on every authenticated request.
    
    Args:
        user_id: The authenticated user's ID
    
    Returns:
        True if Redis was updated, False on error (silent fail)
    """
    try:
        key = f"{ACTIVITY_KEY_PREFIX}{user_id}"
        result = redis_set(key, "1", expire_seconds=ACTIVITY_TTL_SECONDS)
        # DEBUG level — this fires on EVERY request, don't spam logs
        logger.debug(f"Activity updated: user_id={user_id} ttl={ACTIVITY_TTL_SECONDS}s")
        return result
    except Exception as e:
        # Silent fail — activity tracking should never break the main request
        logger.warning(f"Failed to update activity for user_id={user_id}: {e}")
        return False


def is_user_online(user_id: int) -> bool:
    """
    Check if a specific user is currently online (has an active Redis key).
    
    Args:
        user_id: The user ID to check
    
    Returns:
        True if user has been active within the last ACTIVITY_TTL_SECONDS
    """
    try:
        key = f"{ACTIVITY_KEY_PREFIX}{user_id}"
        return bool(redis_client.exists(key))
    except Exception as e:
        logger.warning(f"Failed to check activity for user_id={user_id}: {e}")
        return False


def get_online_customer_ids() -> List[int]:
    """
    Scan Redis for all currently active customer keys.
    Uses SCAN (not KEYS) to avoid blocking Redis on large datasets.
    
    Returns:
        List of user IDs that are currently online
    """
    try:
        online_ids = []
        cursor = 0
        pattern = f"{ACTIVITY_KEY_PREFIX}*"

        # SCAN in batches of 100 — safe for production
        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=100)
            for key in keys:
                # Extract user_id from "last_active:42" → 42
                try:
                    user_id = int(key.replace(ACTIVITY_KEY_PREFIX, ""))
                    online_ids.append(user_id)
                except (ValueError, TypeError):
                    # Skip malformed keys silently
                    continue

            # cursor == 0 means SCAN is complete
            if cursor == 0:
                break

        logger.debug(f"Online customer IDs found: {len(online_ids)}")
        return online_ids

    except Exception as e:
        logger.error(f"Failed to scan online customers from Redis: {e}")
        return []


def get_online_customers_for_creator(creator_user_id: int) -> List[dict]:
    """
    Get list of online customers with their profile details.
    Used by the Creator Dashboard "Online Customers" tab.
    
    - Scans Redis for active customer keys
    - Fetches user details from MySQL (only user_type='customer')
    - Excludes the creator themselves
    - Returns top MAX_ONLINE_CUSTOMERS sorted by name
    
    Args:
        creator_user_id: The creator's user ID (to exclude from results)
    
    Returns:
        List of dicts: [{ id, name, profile_photo }, ...]
    """
    online_ids = get_online_customer_ids()
    
    logger.info(f"[DEBUG] Raw online IDs from Redis: {online_ids}")
    logger.info(f"[DEBUG] Creator user ID (excluded): {creator_user_id}")

    if not online_ids:
        logger.info(f"[DEBUG] No online IDs found in Redis at all")
        return []

    # Remove the creator from the list
    online_ids = [uid for uid in online_ids if uid != creator_user_id]
    logger.info(f"[DEBUG] Online IDs after excluding creator: {online_ids}")

    if not online_ids:
        logger.info(f"[DEBUG] All online users were the creator themselves")
        return []

    online_ids = online_ids[:MAX_ONLINE_CUSTOMERS]

    try:
        placeholders = ",".join(["%s"] * len(online_ids))
        query = f"""
            SELECT id, name, profile_photo
            FROM users
            WHERE id IN ({placeholders})
              AND user_type IN ('customer', 'user')
              AND is_active = 1
              AND is_blocked = 0
            ORDER BY name ASC
            LIMIT {MAX_ONLINE_CUSTOMERS}
        """
        logger.info(f"[DEBUG] Running query with IDs: {online_ids}")
        
        customers = execute_query(query, tuple(online_ids), fetch_all=True)
        
        logger.info(f"[DEBUG] Query returned: {customers}")

        return customers or []

    except Exception as e:
        logger.error(f"Failed to fetch online customer details: {e}", exc_info=True)
        return []