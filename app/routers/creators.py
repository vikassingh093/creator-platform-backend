from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form, Request
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
from app.config import CHAT_PLATFORM_COMMISSION
from app.helpers.wallet_helper import debit_creator_wallet
import logging
from pydantic import BaseModel
from typing import Optional
import os
import shutil
import uuid
from app.services.activity_service import get_online_customers_for_creator
from app.services.file_service import save_file

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

# ── Helper: get commission setting ──────────────────────────────
def get_commission_percent() -> float:
    """
    Reads platform commission % from DB.
    Falls back to CHAT_PLATFORM_COMMISSION from config if not set.
    """
    row = execute_query(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_commission_percent'",
        fetch_one=True
    )
    return float(row["setting_value"]) if row else CHAT_PLATFORM_COMMISSION

def get_creator_share(total_amount: float) -> tuple[float, float]:
    """Returns (creator_amount, platform_commission)"""
    commission_pct = get_commission_percent()
    commission = round(total_amount * commission_pct / 100, 2)
    creator_amt = round(total_amount - commission, 2)
    return creator_amt, commission

# ── Helper: build full photo URL ──────────────────────────────────
def make_photo_url(request: Request, path: str) -> str:
    """Convert /uploads/... to http://host:port/uploads/..."""
    if not path:
        return None
    if path.startswith("http"):
        return path
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"

@router.get("/")
def get_all_creators(
    category: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
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

    base_query += " ORDER BY cp.is_online DESC, cp.rating DESC"
    base_query += f" LIMIT {limit} OFFSET {offset}"

    creators = execute_query(base_query, tuple(params), fetch_all=True)
    return {"success": True, "page": page, "limit": limit, "creators": creators or []}


@router.get("/categories")
def get_categories(current_user: dict = Depends(get_current_user)):
    return {"success": True, "categories": ["All", "Astrology", "Entertainment", "Fashion"]}


# ── IMPORTANT: static routes MUST be before /{creator_id} ──

@router.post("/photo/upload")
async def upload_profile_photo(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    existing = execute_query(
        "SELECT photo_status FROM users WHERE id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if existing and existing["photo_status"] == "pending":
        raise HTTPException(status_code=400, detail="You already have a photo pending approval. Please wait.")

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")

    allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, WEBP images allowed")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"

    photo_dir = os.path.join(UPLOAD_DIR, "profile_photos")
    os.makedirs(photo_dir, exist_ok=True)
    save_path = os.path.join(photo_dir, filename)

    with open(save_path, "wb") as f:
        f.write(contents)

    file_url = f"/uploads/profile_photos/{filename}"

    execute_query(
        """
        UPDATE users
        SET pending_photo = %s, photo_status = 'pending', photo_reject_reason = NULL
        WHERE id = %s
        """,
        (file_url, current_user["id"])
    )

    logger.info(f"📸 Creator {current_user['id']} uploaded profile photo: {file_url}")

    return {
        "success": True,
        "message": "Photo uploaded! Pending admin approval.",
        "pending_photo": file_url,
        "photo_status": "pending"
    }


@router.get("/photo/status")
def get_photo_status(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    user = execute_query(
        "SELECT profile_photo, pending_photo, photo_status, photo_reject_reason FROM users WHERE id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    return {
        "success": True,
        "profile_photo": user.get("profile_photo"),
        "pending_photo": user.get("pending_photo"),
        "photo_status": user.get("photo_status", "none"),
        "reject_reason": user.get("photo_reject_reason"),
    }


@router.get("/online-customers")
def get_online_customers(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    try:
        customers = get_online_customers_for_creator(current_user["id"])
        logger.info(
            f"Online customers requested by creator_id={current_user['id']}: "
            f"{len(customers)} found"
        )
        return {
            "success": True,
            "customers": customers,
            "count": len(customers)
        }
    except Exception as e:
        logger.error(f"Error fetching online customers for creator_id={current_user['id']}: {e}", exc_info=True)
        return {"success": True, "customers": [], "count": 0}


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


@router.get("/commission")
def get_commission(current_user: dict = Depends(get_current_user)):
    commission_pct = get_commission_percent()
    return {
        "success": True,
        "platform_commission_percent": commission_pct,
        "creator_share_percent": 100 - commission_pct
    }


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
# 🔴 FIX #11: Uses atomic debit_creator_wallet + records NO transaction yet.
# Transaction is recorded by admin.py when admin APPROVES the withdrawal.
# This prevents recording a withdrawal transaction before it's actually paid out.

@router.post("/withdrawal/request")
def request_withdrawal(
    body: WithdrawalRequest,
    current_user: dict = Depends(get_current_user)
):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")

    if body.amount < 100:
        raise HTTPException(status_code=400, detail="Minimum withdrawal amount is ₹100")

    if body.amount > 50000:
        raise HTTPException(status_code=400, detail="Maximum withdrawal amount is ₹50,000 per request")

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

    # 🔴 FIX #11: Atomic debit FIRST — if balance dropped between check and here, this fails safely
    result = debit_creator_wallet(current_user["id"], body.amount)
    if result is None:
        raise HTTPException(status_code=400, detail="Insufficient balance (concurrent request)")

    # Now insert withdrawal request (balance already deducted)
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

    logger.info(f"💸 Withdrawal request: Creator {current_user['id']} | ₹{body.amount} | {body.method}")

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


UPLOAD_DIR = "uploads"
os.makedirs(f"{UPLOAD_DIR}/images", exist_ok=True)
os.makedirs(f"{UPLOAD_DIR}/videos", exist_ok=True)

# Content upload routes commented out (unchanged)


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