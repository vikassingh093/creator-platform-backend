from app.database import execute_query
import logging

logger = logging.getLogger(__name__)


def create_notification(user_id: int, title: str, message: str, type: str = "system", reference_id: str = None):
    try:
        execute_query(
            """
            INSERT INTO notifications (user_id, title, message, type, is_read, reference_id)
            VALUES (%s, %s, %s, %s, 0, %s)
            """,
            (user_id, title, message, type, reference_id)
        )
        logger.info(f"Notification created for user {user_id}: {title}")
    except Exception as e:
        logger.error(f"Failed to create notification: {e}")