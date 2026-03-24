from fastapi import APIRouter, Depends, HTTPException
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/")
def get_notifications(current_user: dict = Depends(get_current_user)):
    notifications = execute_query(
        """
        SELECT id, title, message, type, is_read, reference_id, created_at
        FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (current_user["id"],),
        fetch_all=True
    )
    unread_count = execute_query(
        "SELECT COUNT(*) AS count FROM notifications WHERE user_id = %s AND is_read = 0",
        (current_user["id"],),
        fetch_one=True
    )
    return {
        "success": True,
        "notifications": notifications or [],
        "unread_count": unread_count["count"] if unread_count else 0
    }


@router.post("/read-all")
def mark_all_read(current_user: dict = Depends(get_current_user)):
    execute_query(
        "UPDATE notifications SET is_read = 1 WHERE user_id = %s",
        (current_user["id"],)
    )
    return {"success": True, "message": "All notifications marked as read"}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, current_user: dict = Depends(get_current_user)):
    execute_query(
        "UPDATE notifications SET is_read = 1 WHERE id = %s AND user_id = %s",
        (notification_id, current_user["id"])
    )
    return {"success": True}


@router.delete("/clear")
def clear_notifications(current_user: dict = Depends(get_current_user)):
    execute_query(
        "DELETE FROM notifications WHERE user_id = %s",
        (current_user["id"],)
    )
    return {"success": True, "message": "Notifications cleared"}