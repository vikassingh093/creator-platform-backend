from fastapi import APIRouter, HTTPException, Depends, Query
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/creators", tags=["Creators"])

@router.get("/")
def get_all_creators(
    category: str = Query(None),
    search: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    offset = (page - 1) * limit

    # If current user is creator - return empty list
    if current_user["user_type"] == "creator":
        return {"success": True, "page": page, "limit": limit, "creators": []}

    base_query = """
        SELECT 
            u.id, u.name, u.profile_photo, u.is_active,
            cp.specialty AS category, cp.bio, cp.chat_rate, cp.call_rate,
            cp.is_online AS is_available, cp.is_approved, cp.rating, cp.total_reviews
        FROM users u
        JOIN creator_profiles cp ON u.id = cp.user_id
        WHERE u.is_active = 1 AND u.is_blocked = 0
        AND cp.is_approved = 1 AND cp.is_rejected = 0
        AND u.id != %s
    """
    params = [current_user["id"]]

    if category and category != "All":
        base_query += " AND cp.specialty = %s"
        params.append(category)

    if search:
        base_query += " AND (u.name LIKE %s OR cp.bio LIKE %s OR cp.specialty LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    base_query += " ORDER BY cp.is_online DESC, cp.rating DESC"
    base_query += f" LIMIT {limit} OFFSET {offset}"

    creators = execute_query(base_query, tuple(params), fetch_all=True)

    return {
        "success": True,
        "page": page,
        "limit": limit,
        "creators": creators or []
    }

@router.get("/categories")
def get_categories(current_user: dict = Depends(get_current_user)):
    categories = execute_query(
        "SELECT DISTINCT specialty FROM creator_profiles WHERE is_approved = 1 AND is_rejected = 0 ORDER BY specialty",
        fetch_all=True
    )
    result = ["All"] + [c["specialty"] for c in categories if c["specialty"]]
    return {"success": True, "categories": result}

# ── IMPORTANT: dashboard & toggle-online MUST be before /{creator_id} ──

@router.get("/dashboard")
def get_creator_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    profile = execute_query(
        "SELECT * FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )

    today_earnings = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE user_id = %s AND type IN ('chat', 'call')
        AND DATE(created_at) = CURDATE()
        """,
        (current_user["id"],),
        fetch_one=True
    )

    total_earnings = execute_query(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE user_id = %s AND type IN ('chat', 'call')
        """,
        (current_user["id"],),
        fetch_one=True
    )

    total_chats = execute_query(
        "SELECT COUNT(*) AS total FROM chat_rooms WHERE creator_id = %s",
        (current_user["id"],),
        fetch_one=True
    )

    return {
        "success": True,
        "is_online": bool(profile["is_online"]) if profile else False,
        "stats": {
            "today_earnings": float(today_earnings["total"]) if today_earnings else 0,
            "total_earnings": float(total_earnings["total"]) if total_earnings else 0,
            "total_chats": total_chats["total"] if total_chats else 0,
            "rating": float(profile["rating"]) if profile and profile["rating"] else 0.0,
        }
    }

@router.post("/toggle-online")
def toggle_online(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    profile = execute_query(
        "SELECT is_online FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    new_status = 0 if profile["is_online"] else 1
    execute_query(
        "UPDATE creator_profiles SET is_online = %s WHERE user_id = %s",
        (new_status, current_user["id"])
    )
    return {"success": True, "is_online": bool(new_status)}

@router.get("/{creator_id}/reviews")
def get_creator_reviews(
    creator_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(get_current_user)
):
    offset = (page - 1) * limit
    try:
        reviews = execute_query(
            """
            SELECT 
                r.id, r.rating, r.comment AS review, r.created_at,
                u.name AS user_name, u.profile_photo AS user_photo
            FROM reviews r
            JOIN users u ON r.user_id = u.id
            WHERE r.creator_id = %s
            ORDER BY r.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (creator_id, limit, offset),
            fetch_all=True
        )
        return {"success": True, "reviews": reviews or []}
    except Exception as e:
        logger.error(f"Reviews error: {e}")
        return {"success": True, "reviews": []}

@router.get("/{creator_id}/content")
def get_creator_content(
    creator_id: int,
    current_user: dict = Depends(get_current_user)
):
    try:
        content = execute_query(
            """
            SELECT 
                c.id, c.title, c.type AS content_type, c.price, c.is_free,
                c.thumbnail, c.duration, c.created_at,
                cf.file_url, cf.file_order
            FROM content c
            LEFT JOIN content_files cf ON c.id = cf.content_id
            WHERE c.creator_id = %s
            ORDER BY c.created_at DESC, cf.file_order ASC
            LIMIT 20
            """,
            (creator_id,),
            fetch_all=True
        )
        return {"success": True, "content": content or []}
    except Exception as e:
        logger.error(f"Content error: {e}")
        return {"success": True, "content": []}

@router.get("/{creator_id}")
def get_creator_profile(
    creator_id: int,
    current_user: dict = Depends(get_current_user)
):
    creator = execute_query(
        """
        SELECT 
            u.id, u.name, u.profile_photo,
            cp.specialty AS category, cp.bio, cp.chat_rate, cp.call_rate,
            cp.is_online AS is_available, cp.is_approved, cp.rating, cp.total_reviews
        FROM users u
        JOIN creator_profiles cp ON u.id = cp.user_id
        WHERE u.id = %s AND u.is_blocked = 0
        AND cp.is_approved = 1 AND cp.is_rejected = 0
        """,
        (creator_id,),
        fetch_one=True
    )

    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    return {"success": True, "creator": creator}