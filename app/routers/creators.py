from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
from app.config import CHAT_PLATFORM_COMMISSION  # ✅ ADD THIS
import logging
from pydantic import BaseModel
from typing import Optional
import os
import shutil
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/creators", tags=["Creators"])

class ReviewRequest(BaseModel):
    rating: float
    comment: str = ""

class WithdrawalRequest(BaseModel):
    amount: float
    method: str  # upi | bank
    upi_id: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    account_holder: Optional[str] = None

# ── Helper: get commission setting ──────────────────────────
def get_commission_percent() -> float:
    """
    Reads platform commission % from DB.
    Falls back to CHAT_PLATFORM_COMMISSION from config if not set.
    To change: UPDATE platform_settings SET setting_value='50' 
               WHERE setting_key='platform_commission_percent'
    """
    row = execute_query(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_commission_percent'",
        fetch_one=True
    )
    return float(row["setting_value"]) if row else CHAT_PLATFORM_COMMISSION  # ✅ was 30.0

def get_creator_share(total_amount: float) -> tuple[float, float]:
    """Returns (creator_amount, platform_commission)"""
    commission_pct = get_commission_percent()
    commission = round(total_amount * commission_pct / 100, 2)
    creator_amt = round(total_amount - commission, 2)
    return creator_amt, commission

@router.get("/")
def get_all_creators(
    category: str = Query(None),
    search: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    offset = (page - 1) * limit

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
    return {"success": True, "page": page, "limit": limit, "creators": creators or []}


@router.get("/categories")
def get_categories(current_user: dict = Depends(get_current_user)):
    categories = execute_query(
        "SELECT DISTINCT specialty FROM creator_profiles WHERE is_approved = 1 AND is_rejected = 0 ORDER BY specialty",
        fetch_all=True
    )
    result = ["All"] + [c["specialty"] for c in categories if c["specialty"]]
    return {"success": True, "categories": result}


# ── IMPORTANT: static routes MUST be before /{creator_id} ──

@router.get("/dashboard")
def get_creator_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    try:
        profile = execute_query(
            "SELECT * FROM creator_profiles WHERE user_id = %s",
            (current_user["id"],),
            fetch_one=True
        )
        if not profile:
            raise HTTPException(status_code=404, detail="Creator profile not found")

        today_earnings = execute_query(
            """
            SELECT COALESCE(SUM(creator_amount), 0) AS total
            FROM transactions
            WHERE creator_id = %s AND type IN ('chat', 'call')
            AND DATE(created_at) = CURDATE()
            """,
            (current_user["id"],),
            fetch_one=True
        )

        total_earnings = execute_query(
            """
            SELECT COALESCE(SUM(creator_amount), 0) AS total
            FROM transactions
            WHERE creator_id = %s AND type IN ('chat', 'call')
            """,
            (current_user["id"],),
            fetch_one=True
        )

        total_chats = execute_query(
            "SELECT COUNT(*) AS total FROM chat_rooms WHERE creator_id = %s",
            (current_user["id"],),
            fetch_one=True
        )

        wallet = execute_query(
            "SELECT * FROM creator_wallet WHERE creator_id = %s",
            (current_user["id"],),
            fetch_one=True
        )

        # ✅ Use profile["id"] (creator_profiles.id) for reviews
        recent_reviews = execute_query(
            """
            SELECT r.id, r.rating, r.comment, r.created_at,
                   u.name AS user_name, u.profile_photo AS user_photo
            FROM reviews r
            JOIN users u ON r.user_id = u.id
            WHERE r.creator_id = %s
            ORDER BY r.created_at DESC
            LIMIT 20
            """,
            (profile["id"],),
            fetch_all=True
        )

        recent_chats = execute_query(
            """
            SELECT cr.id, cr.created_at, cr.status,
                   u.name AS user_name, u.profile_photo AS user_photo
            FROM chat_rooms cr
            JOIN users u ON cr.user_id = u.id
            WHERE cr.creator_id = %s
            ORDER BY cr.created_at DESC
            LIMIT 5
            """,
            (current_user["id"],),
            fetch_all=True
        )

        commission_pct = get_commission_percent()

        return {
            "success": True,
            "is_online": bool(profile["is_online"]),
            "commission_percent": commission_pct,
            "creator_share_percent": 100 - commission_pct,
            "stats": {
                "today_earnings": float(today_earnings["total"]) if today_earnings else 0,
                "total_earnings": float(total_earnings["total"]) if total_earnings else 0,
                "total_chats": total_chats["total"] if total_chats else 0,
                "rating": float(profile["rating"]) if profile["rating"] else 0.0,
            },
            "wallet": {
                "balance": float(wallet["balance"]) if wallet else 0.0,
                "total_earned": float(wallet["total_earned"]) if wallet else 0.0,
                "total_withdrawn": float(wallet["total_withdrawn"]) if wallet else 0.0,
            },
            "recent_reviews": recent_reviews or [],
            "recent_chats": recent_chats or [],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard error for user {current_user['id']}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Commission endpoint (admin can update) ──────────────────

@router.get("/commission")
def get_commission(current_user: dict = Depends(get_current_user)):
    commission_pct = get_commission_percent()
    return {
        "success": True,
        "platform_commission_percent": commission_pct,
        "creator_share_percent": 100 - commission_pct
    }


# ── Wallet ───────────────────────────────────────────────────

@router.get("/wallet")
def get_creator_wallet(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    wallet = execute_query(
        "SELECT * FROM creator_wallet WHERE creator_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    return {
        "success": True,
        "wallet": {
            "balance": float(wallet["balance"]) if wallet else 0.0,
            "total_earned": float(wallet["total_earned"]) if wallet else 0.0,
            "total_withdrawn": float(wallet["total_withdrawn"]) if wallet else 0.0,
        }
    }


# ── Withdrawal ───────────────────────────────────────────────

@router.post("/withdrawal/request")
def request_withdrawal(
    body: WithdrawalRequest,
    current_user: dict = Depends(get_current_user)
):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    if body.amount < 100:
        raise HTTPException(status_code=400, detail="Minimum withdrawal amount is ₹100")

    wallet = execute_query(
        "SELECT * FROM creator_wallet WHERE creator_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    available = float(wallet["balance"]) if wallet else 0.0
    if available < body.amount:
        raise HTTPException(status_code=400, detail=f"Insufficient balance. Available: ₹{available}")

    pending = execute_query(
        "SELECT id FROM withdrawal_requests WHERE creator_id = %s AND status = 'pending'",
        (current_user["id"],),
        fetch_one=True
    )
    if pending:
        raise HTTPException(status_code=400, detail="You already have a pending withdrawal request")

    if body.method == "upi" and not body.upi_id:
        raise HTTPException(status_code=400, detail="UPI ID is required")
    if body.method == "bank" and not all([body.bank_name, body.account_number, body.ifsc_code]):
        raise HTTPException(status_code=400, detail="Bank details are required")

    execute_query(
        """
        INSERT INTO withdrawal_requests 
        (creator_id, amount, method, upi_id, bank_name, account_number, ifsc_code, account_holder)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (current_user["id"], body.amount, body.method,
         body.upi_id, body.bank_name, body.account_number,
         body.ifsc_code, body.account_holder)
    )
    execute_query(
        "UPDATE creator_wallet SET balance = balance - %s WHERE creator_id = %s",
        (body.amount, current_user["id"])
    )

    return {"success": True, "message": "Withdrawal request submitted successfully"}


@router.get("/withdrawal/history")
def get_withdrawal_history(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    requests = execute_query(
        """
        SELECT id, amount, method, upi_id, bank_name, account_number,
               ifsc_code, account_holder, status, admin_note, created_at, updated_at
        FROM withdrawal_requests
        WHERE creator_id = %s
        ORDER BY created_at DESC
        """,
        (current_user["id"],),
        fetch_all=True
    )
    return {"success": True, "requests": requests or []}


# ── Toggle Online ────────────────────────────────────────────

@router.post("/toggle-online")
def toggle_online(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    profile = execute_query(
        "SELECT id, is_online FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator profile not found")

    new_status = 0 if profile["is_online"] else 1
    execute_query(
        "UPDATE creator_profiles SET is_online = %s WHERE user_id = %s",
        (new_status, current_user["id"])
    )
    return {"success": True, "is_online": bool(new_status)}


# ── Content Upload ───────────────────────────────────────────

UPLOAD_DIR = "uploads"
os.makedirs(f"{UPLOAD_DIR}/images", exist_ok=True)
os.makedirs(f"{UPLOAD_DIR}/videos", exist_ok=True)

@router.post("/content/upload")
async def upload_content(
    title: str = Form(...),
    description: str = Form(""),
    content_type: str = Form(...),
    price: float = Form(0.0),
    is_free: int = Form(0),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    type_map = {
        "image": "photo",
        "photo": "photo",
        "photo_pack": "photo_pack",
        "video": "video"
    }
    if content_type not in type_map:
        raise HTTPException(status_code=400, detail="Invalid content type. Use: image, photo, photo_pack, video")

    db_type = type_map[content_type]

    allowed_images = ["image/jpeg", "image/png", "image/webp", "image/gif"]
    allowed_videos = ["video/mp4", "video/webm", "video/quicktime"]

    if db_type in ["photo", "photo_pack"] and file.content_type not in allowed_images:
        raise HTTPException(status_code=400, detail="Invalid image format. Use JPG, PNG, WEBP")
    if db_type == "video" and file.content_type not in allowed_videos:
        raise HTTPException(status_code=400, detail="Invalid video format. Use MP4, WEBM")

    ext = file.filename.rsplit(".", 1)[-1].lower()
    filename = f"{uuid.uuid4()}.{ext}"
    folder = "videos" if db_type == "video" else "images"
    save_path = os.path.join(UPLOAD_DIR, folder, filename)

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_url = f"/uploads/{folder}/{filename}"
    thumbnail = file_url if db_type != "video" else None

    execute_query(
        """
        INSERT INTO content (creator_id, title, description, type, price, is_free, thumbnail, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        """,
        (current_user["id"], title, description, db_type, price, is_free, thumbnail)
    )

    content_row = execute_query(
        "SELECT id FROM content WHERE creator_id = %s ORDER BY created_at DESC LIMIT 1",
        (current_user["id"],),
        fetch_one=True
    )

    if not content_row:
        raise HTTPException(status_code=500, detail="Failed to save content record")

    execute_query(
        "INSERT INTO content_files (content_id, file_url, file_order) VALUES (%s, %s, 0)",
        (content_row["id"], file_url)
    )

    return {
        "success": True,
        "message": "Content uploaded! Pending admin approval",
        "file_url": file_url,
        "content_id": content_row["id"]
    }


@router.get("/content/my")
def get_my_content(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    content = execute_query(
        """
        SELECT c.id, c.title, c.description, c.type AS content_type,
               c.price, c.is_free, c.thumbnail, c.status, c.created_at,
               cf.file_url
        FROM content c
        LEFT JOIN content_files cf ON c.id = cf.content_id AND cf.file_order = 0
        WHERE c.creator_id = %s
        ORDER BY c.created_at DESC
        """,
        (current_user["id"],),
        fetch_all=True
    )
    return {"success": True, "content": content or []}


# ── Reviews ──────────────────────────────────────────────────

@router.get("/{creator_id}/reviews")
def get_creator_reviews(
    creator_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(get_current_user)
):
    offset = (page - 1) * limit
    try:
        # creator_id param is users.id → get creator_profiles.id
        creator_profile = execute_query(
            "SELECT id FROM creator_profiles WHERE user_id = %s",
            (creator_id,),
            fetch_one=True
        )
        if not creator_profile:
            return {"success": True, "reviews": [], "total": 0}

        total = execute_query(
            "SELECT COUNT(*) AS total FROM reviews WHERE creator_id = %s",
            (creator_profile["id"],),
            fetch_one=True
        )

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
            (creator_profile["id"], limit, offset),
            fetch_all=True
        )
        return {
            "success": True,
            "reviews": reviews or [],
            "total": total["total"] if total else 0,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        logger.error(f"Reviews error: {e}", exc_info=True)
        return {"success": True, "reviews": [], "total": 0}


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
            WHERE c.creator_id = %s AND c.status = 'approved'
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
            cp.id AS profile_id, cp.specialty AS category, cp.bio,
            cp.chat_rate, cp.call_rate,
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


@router.post("/{creator_id}/review")
def submit_review(
    creator_id: int,
    body: ReviewRequest,
    current_user: dict = Depends(get_current_user)
):
    if current_user["user_type"] == "creator":
        raise HTTPException(status_code=403, detail="Creators cannot submit reviews")

    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    creator_profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (creator_id,),
        fetch_one=True
    )
    if not creator_profile:
        raise HTTPException(status_code=404, detail="Creator not found")

    profile_id = creator_profile["id"]

    chat = execute_query(
        "SELECT id FROM chat_rooms WHERE user_id = %s AND creator_id = %s LIMIT 1",
        (current_user["id"], creator_id),
        fetch_one=True
    )
    if not chat:
        raise HTTPException(status_code=403, detail="You must chat with the creator before reviewing")

    existing = execute_query(
        "SELECT id FROM reviews WHERE user_id = %s AND creator_id = %s",
        (current_user["id"], profile_id),
        fetch_one=True
    )

    if existing:
        execute_query(
            "UPDATE reviews SET rating = %s, comment = %s WHERE user_id = %s AND creator_id = %s",
            (body.rating, body.comment, current_user["id"], profile_id)
        )
    else:
        execute_query(
            "INSERT INTO reviews (user_id, creator_id, rating, comment) VALUES (%s, %s, %s, %s)",
            (current_user["id"], profile_id, body.rating, body.comment)
        )

    # Update avg rating
    execute_query(
        """
        UPDATE creator_profiles 
        SET 
            rating = (SELECT AVG(rating) FROM reviews WHERE creator_id = %s),
            total_reviews = (SELECT COUNT(*) FROM reviews WHERE creator_id = %s)
        WHERE id = %s
        """,
        (profile_id, profile_id, profile_id)
    )

    return {"success": True, "message": "Review submitted successfully"}

# In your chat/call session end handler (wherever sessions are ended)

def credit_creator_for_session(creator_user_id: int, user_id: int, total_charged: float, session_type: str, room_id: int = None):
    """Call this when a chat/call session ends"""
    if total_charged <= 0:
        logger.warning(f"Skipping credit — total_charged is {total_charged}")
        return 0, 0

    creator_amount, commission = get_creator_share(total_charged)

    # ✅ Credit creator wallet
    execute_query(
        """
        INSERT INTO creator_wallet (creator_id, balance, total_earned)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE 
            balance = balance + VALUES(balance),
            total_earned = total_earned + VALUES(total_earned)
        """,
        (creator_user_id, creator_amount, creator_amount)
    )

    # ✅ Log transaction — user_id is the customer, creator_id is the creator
    execute_query(
        """
        INSERT INTO transactions 
            (user_id, creator_id, type, amount, creator_amount, commission_amount, description, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'success')
        """,
        (
            user_id,
            creator_user_id,
            session_type,
            total_charged,
            creator_amount,
            commission,
            f"{session_type.capitalize()} session - Room #{room_id}" if room_id else f"{session_type.capitalize()} session"
        )
    )

    logger.info(
        f"✅ Session credited: type={session_type} room={room_id} "
        f"total=₹{total_charged} creator=₹{creator_amount} commission=₹{commission}"
    )
    return creator_amount, commission