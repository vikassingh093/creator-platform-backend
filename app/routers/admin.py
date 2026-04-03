from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from app.middleware.auth_middleware import require_admin
from app.database import execute_query

from app.helpers.wallet_helper import (
    credit_wallet, debit_creator_wallet, ensure_creator_wallet_exists
)
from app.helpers.transaction_helper import (
    record_refund, record_withdrawal, calculate_split
)

import logging
from typing import Optional
from datetime import date, timedelta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


class ApproveCreatorRequest(BaseModel):
    action: str

class BlockUserRequest(BaseModel):
    is_blocked: bool

class WithdrawalActionRequest(BaseModel):
    action: str
    note: Optional[str] = None

class CommissionUpdateRequest(BaseModel):
    commission_percent: float

class AdminRefundRequest(BaseModel):
    user_id: int
    amount: float
    reason: str
    reference_id: Optional[str] = None
    creator_id: Optional[int] = None

class PhotoActionRequest(BaseModel):
    action: str  # approve | reject
    reason: Optional[str] = None


# ── Stats (with date range) ──────────────────────────────────

@router.get("/stats")
def get_stats(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    current_user: dict = Depends(require_admin)
):
    """
    Full platform stats with optional date range filtering.
    If no dates provided, returns all-time stats + today stats.
    """

    # Build date filter clause
    date_filter = ""
    date_params = []
    if date_from and date_to:
        date_filter = " AND DATE(created_at) BETWEEN %s AND %s"
        date_params = [date_from, date_to]
    elif date_from:
        date_filter = " AND DATE(created_at) >= %s"
        date_params = [date_from]
    elif date_to:
        date_filter = " AND DATE(created_at) <= %s"
        date_params = [date_to]

    # ── All-time counts (not date filtered) ──
    total_users = execute_query(
        "SELECT COUNT(*) as count FROM users WHERE user_type = 'user'",
        fetch_one=True
    )
    total_creators = execute_query(
        "SELECT COUNT(*) as count FROM creator_profiles WHERE is_approved = TRUE",
        fetch_one=True
    )
    pending_creators = execute_query(
        "SELECT COUNT(*) as count FROM creator_profiles WHERE is_approved = FALSE AND is_rejected = FALSE",
        fetch_one=True
    )
    pending_withdrawals = execute_query(
        "SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM withdrawal_requests WHERE status = 'pending'",
        fetch_one=True
    )

    # ── Date-filtered stats ──

    # Total deposits (add_money)
    total_deposits = execute_query(
        f"SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count FROM transactions WHERE type = 'add_money' AND status = 'success'{date_filter}",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Commission earned
    commission_earned = execute_query(
        f"SELECT COALESCE(SUM(commission_amount), 0) as total FROM transactions WHERE status = 'success' AND commission_amount > 0{date_filter}",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Creator payouts (approved withdrawals)
    wd_date_filter = date_filter.replace("created_at", "updated_at") if date_filter else ""
    total_payouts = execute_query(
        f"SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count FROM withdrawal_requests WHERE status = 'approved'{wd_date_filter}",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Withdrawal requests in range
    withdrawals_in_range = execute_query(
        f"SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM withdrawal_requests WHERE 1=1{date_filter.replace('created_at', 'created_at')}",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Total transactions count
    total_transactions = execute_query(
        f"SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM transactions WHERE 1=1{date_filter}",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Users registered in range
    users_registered = execute_query(
        f"SELECT COUNT(*) as count FROM users WHERE 1=1{date_filter}",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # ── Call breakdown: Audio vs Video ──
    audio_calls = execute_query(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total, 
            COALESCE(SUM(commission_amount), 0) as commission
            FROM transactions WHERE type = 'call' AND status = 'success'
            AND (description LIKE '%%audio%%' OR description LIKE '%%Audio%%'){date_filter}""",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    video_calls = execute_query(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total,
            COALESCE(SUM(commission_amount), 0) as commission
            FROM transactions WHERE type = 'call' AND status = 'success'
            AND (description LIKE '%%video%%' OR description LIKE '%%Video%%'){date_filter}""",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # All calls (for those without audio/video in description)
    all_calls = execute_query(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total,
            COALESCE(SUM(commission_amount), 0) as commission
            FROM transactions WHERE type = 'call' AND status = 'success'{date_filter}""",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Chat sessions
    chat_sessions = execute_query(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total,
            COALESCE(SUM(commission_amount), 0) as commission
            FROM transactions WHERE type = 'chat' AND status = 'success'{date_filter}""",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # Refunds
    refunds = execute_query(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total
            FROM transactions WHERE type = 'refund'{date_filter}""",
        tuple(date_params) if date_params else None,
        fetch_one=True
    )

    # ── Today stats (always) ──
    today_deposits = execute_query(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = 'add_money' AND status = 'success' AND DATE(created_at) = CURDATE()",
        fetch_one=True
    )
    today_commission = execute_query(
        "SELECT COALESCE(SUM(commission_amount), 0) as total FROM transactions WHERE status = 'success' AND commission_amount > 0 AND DATE(created_at) = CURDATE()",
        fetch_one=True
    )

    return {
        "success": True,
        "date_range": {
            "from": date_from,
            "to": date_to,
            "is_filtered": bool(date_from or date_to)
        },
        "data": {
            # All-time
            "total_users": total_users["count"],
            "total_creators": total_creators["count"],
            "pending_approvals": pending_creators["count"],
            "pending_withdrawals_count": pending_withdrawals["count"],
            "pending_withdrawals_amount": float(pending_withdrawals["total"]),

            # Date-filtered
            "total_deposits": float(total_deposits["total"]),
            "total_deposits_count": total_deposits["count"],
            "commission_earned": float(commission_earned["total"]),
            "total_payouts": float(total_payouts["total"]),
            "total_payouts_count": total_payouts["count"],
            "withdrawals_in_range_count": withdrawals_in_range["count"],
            "withdrawals_in_range_amount": float(withdrawals_in_range["total"]),
            "total_transactions_count": total_transactions["count"],
            "total_transactions_amount": float(total_transactions["total"]),
            "users_registered": users_registered["count"],

            # Calls breakdown
            "audio_calls_count": audio_calls["count"],
            "audio_calls_revenue": float(audio_calls["total"]),
            "audio_calls_commission": float(audio_calls["commission"]),
            "video_calls_count": video_calls["count"],
            "video_calls_revenue": float(video_calls["total"]),
            "video_calls_commission": float(video_calls["commission"]),
            "all_calls_count": all_calls["count"],
            "all_calls_revenue": float(all_calls["total"]),
            "all_calls_commission": float(all_calls["commission"]),

            # Chat
            "chat_sessions_count": chat_sessions["count"],
            "chat_sessions_revenue": float(chat_sessions["total"]),
            "chat_sessions_commission": float(chat_sessions["commission"]),

            # Refunds
            "refunds_count": refunds["count"],
            "refunds_amount": float(refunds["total"]),

            # Today (always)
            "today_deposits": float(today_deposits["total"]),
            "today_commission": float(today_commission["total"]),
        }
    }


# ── Creators ──────────────────────────────────────────────

@router.get("/creators")
def get_all_creators(
    status: str = "all",
    current_user: dict = Depends(require_admin)
):
    conditions = []
    values = []

    if status == "pending":
        conditions.append("cp.is_approved = FALSE AND cp.is_rejected = FALSE")
    elif status == "approved":
        conditions.append("cp.is_approved = TRUE")
    elif status == "rejected":
        conditions.append("cp.is_rejected = TRUE")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    creators = execute_query(
        f"""
        SELECT u.id, u.name, u.phone, u.email, u.profile_photo, u.created_at,
               cp.id as creator_profile_id, cp.specialty, cp.bio,
               cp.is_approved, cp.is_rejected, cp.total_earnings,
               cp.rating, cp.total_reviews
        FROM users u
        JOIN creator_profiles cp ON cp.user_id = u.id
        {where}
        ORDER BY cp.created_at DESC
        """,
        tuple(values) if values else None,
        fetch_all=True
    )
    return {"success": True, "data": creators}


@router.put("/creators/{creator_id}/approve")
def approve_reject_creator(
    creator_id: int,
    body: ApproveCreatorRequest,
    current_user: dict = Depends(require_admin)
):
    if body.action == "approve":
        execute_query(
            "UPDATE creator_profiles SET is_approved = TRUE, is_rejected = FALSE WHERE id = %s",
            (creator_id,)
        )
        execute_query(
            """
            UPDATE users u JOIN creator_profiles cp ON cp.user_id = u.id
            SET u.user_type = 'creator' WHERE cp.id = %s
            """,
            (creator_id,)
        )
        creator_user = execute_query(
            "SELECT user_id FROM creator_profiles WHERE id = %s",
            (creator_id,),
            fetch_one=True
        )
        if creator_user:
            ensure_creator_wallet_exists(creator_user["user_id"])

        return {"success": True, "message": "Creator approved!"}
    elif body.action == "reject":
        execute_query(
            "UPDATE creator_profiles SET is_approved = FALSE, is_rejected = TRUE WHERE id = %s",
            (creator_id,)
        )
        return {"success": True, "message": "Creator rejected!"}

    raise HTTPException(status_code=400, detail="Action must be approve or reject")


# ── Users ─────────────────────────────────────────────────

@router.get("/users")
def get_all_users(current_user: dict = Depends(require_admin)):
    users = execute_query(
        """
        SELECT u.id, u.name, u.phone, u.user_type,
               u.is_blocked, u.created_at,
               w.balance as wallet_balance
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.id
        WHERE u.user_type = 'user'
        ORDER BY u.created_at DESC
        """,
        fetch_all=True
    )
    return {"success": True, "data": users}


@router.put("/users/{user_id}/block")
def block_unblock_user(
    user_id: int,
    body: BlockUserRequest,
    current_user: dict = Depends(require_admin)
):
    execute_query(
        "UPDATE users SET is_blocked = %s WHERE id = %s",
        (body.is_blocked, user_id)
    )
    action = "blocked" if body.is_blocked else "unblocked"
    return {"success": True, "message": f"User {action} successfully"}


# ── Withdrawal Management ─────────────────────────────────

@router.get("/withdrawals")
def get_withdrawals(
    status: str = "all",
    current_user: dict = Depends(require_admin)
):
    condition = ""
    params = []
    if status != "all":
        condition = "WHERE wr.status = %s"
        params.append(status)

    withdrawals = execute_query(
        f"""
        SELECT wr.id, wr.amount, wr.method, wr.upi_id, wr.bank_name,
               wr.account_number, wr.ifsc_code, wr.account_holder,
               wr.status, wr.admin_note, wr.created_at, wr.updated_at,
               u.name AS creator_name, u.phone AS creator_phone, u.id AS creator_user_id,
               cw.balance AS creator_balance, cw.total_earned, cw.total_withdrawn
        FROM withdrawal_requests wr
        JOIN users u ON u.id = wr.creator_id
        LEFT JOIN creator_wallet cw ON cw.creator_id = wr.creator_id
        {condition}
        ORDER BY wr.created_at DESC
        """,
        tuple(params) if params else None,
        fetch_all=True
    )
    return {"success": True, "data": withdrawals or []}


@router.put("/withdrawals/{withdrawal_id}")
def process_withdrawal(
    withdrawal_id: int,
    body: WithdrawalActionRequest,
    current_user: dict = Depends(require_admin)
):
    withdrawal = execute_query(
        "SELECT * FROM withdrawal_requests WHERE id = %s",
        (withdrawal_id,),
        fetch_one=True
    )
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Withdrawal not found")
    if withdrawal["status"] != "pending":
        raise HTTPException(status_code=400, detail="Already processed")

    if body.action == "approved":
        # Record transaction for audit trail
        txn_id = record_withdrawal(
            creator_id=withdrawal["creator_id"],
            amount=withdrawal["amount"],
            withdrawal_request_id=withdrawal_id
        )

        # 🔴 FIX #12: Update total_withdrawn (balance was already deducted in creators.py)
        # This is the ONLY place total_withdrawn is incremented
        execute_query(
            "UPDATE creator_wallet SET total_withdrawn = total_withdrawn + %s WHERE creator_id = %s",
            (withdrawal["amount"], withdrawal["creator_id"])
        )

        execute_query(
            """UPDATE withdrawal_requests 
            SET status = 'approved', admin_note = %s, updated_at = NOW() 
            WHERE id = %s""",
            (body.note, withdrawal_id)
        )
        logger.info(f"✅ Withdrawal #{withdrawal_id} approved | ₹{withdrawal['amount']}")

    elif body.action == "rejected":
        # 🔴 FIX #12: Restore balance only (total_withdrawn was never incremented)
        execute_query(
            "UPDATE creator_wallet SET balance = balance + %s WHERE creator_id = %s",
            (withdrawal["amount"], withdrawal["creator_id"])
        )
        execute_query(
            """UPDATE withdrawal_requests 
            SET status = 'rejected', admin_note = %s, updated_at = NOW() 
            WHERE id = %s""",
            (body.note, withdrawal_id)
        )
        logger.info(f"❌ Withdrawal #{withdrawal_id} rejected | ₹{withdrawal['amount']} refunded to balance")
    else:
        raise HTTPException(status_code=400, detail="Action must be approved or rejected")

    return {"success": True, "message": f"Withdrawal {body.action}"}


# ── Admin Refund ──────────────────────────────────────────

@router.post("/refund")
def admin_refund(
    body: AdminRefundRequest,
    current_user: dict = Depends(require_admin)
):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Refund amount must be positive")

    user = execute_query(
        "SELECT id, name FROM users WHERE id = %s",
        (body.user_id,),
        fetch_one=True
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    credit_wallet(body.user_id, body.amount, update_total_added=False)

    txn_id = record_refund(
        user_id=body.user_id,
        amount=body.amount,
        reason=f"Admin refund: {body.reason}",
        reference_id=body.reference_id,
        creator_id=body.creator_id
    )

    logger.info(f"💰 Admin refund: ₹{body.amount} to user {body.user_id} ({user['name']})")

    return {
        "success": True,
        "message": f"₹{body.amount} refunded to {user['name']}",
        "transaction_id": txn_id
    }


# ── Transactions ──────────────────────────────────────────

@router.get("/transactions")
def get_all_transactions(
    type: str = "all",
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = 50,
    current_user: dict = Depends(require_admin)
):
    conditions = []
    params = []

    if type != "all":
        conditions.append("t.type = %s")
        params.append(type)

    if date_from:
        conditions.append("DATE(t.created_at) >= %s")
        params.append(date_from)

    if date_to:
        conditions.append("DATE(t.created_at) <= %s")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(min(limit, 500))

    transactions = execute_query(
        f"""
        SELECT t.id, t.user_id, t.creator_id, t.type, t.amount,
               t.creator_amount, t.commission_amount,
               t.description, t.reference_id, t.status,
               t.razorpay_order_id, t.created_at,
               u.name AS user_name, u.phone AS user_phone,
               c.name AS creator_name
        FROM transactions t
        LEFT JOIN users u ON u.id = t.user_id
        LEFT JOIN users c ON c.id = t.creator_id
        {where}
        ORDER BY t.created_at DESC
        LIMIT %s
        """,
        tuple(params),
        fetch_all=True
    )
    return {"success": True, "data": transactions or []}


# ── Photo Approval Management ─────────────────────────────

@router.get("/photo-approvals")
def get_pending_photos(
    status: str = "pending",
    current_user: dict = Depends(require_admin)
):
    """Get all creators with pending/approved/rejected photos"""
    if status == "all":
        condition = "u.photo_status != 'none'"
    else:
        condition = f"u.photo_status = '{status}'"

    photos = execute_query(
        f"""
        SELECT u.id, u.name, u.phone, u.profile_photo, u.pending_photo,
               u.photo_status, u.photo_reject_reason, u.created_at,
               cp.specialty, cp.rating, cp.total_reviews
        FROM users u
        LEFT JOIN creator_profiles cp ON cp.user_id = u.id
        WHERE {condition}
        ORDER BY 
            CASE u.photo_status 
                WHEN 'pending' THEN 0 
                WHEN 'rejected' THEN 1 
                ELSE 2 
            END,
            u.created_at DESC
        """,
        fetch_all=True
    )
    
    logger.info(f"📸 Photo approvals query status={status}, found={len(photos or [])}")
    return {"success": True, "data": photos or []}


@router.put("/photo-approvals/{user_id}")
def approve_reject_photo(
    user_id: int,
    body: PhotoActionRequest,
    current_user: dict = Depends(require_admin)
):
    """Approve or reject a creator's pending photo"""
    user = execute_query(
        "SELECT id, name, pending_photo, photo_status FROM users WHERE id = %s",
        (user_id,),
        fetch_one=True
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user["pending_photo"]:
        raise HTTPException(status_code=400, detail="No pending photo to review")

    if body.action == "approve":
        # Move pending_photo → profile_photo
        execute_query(
            """
            UPDATE users 
            SET profile_photo = pending_photo, 
                pending_photo = NULL, 
                photo_status = 'approved',
                photo_reject_reason = NULL
            WHERE id = %s
            """,
            (user_id,)
        )
        logger.info(f"✅ Admin approved photo for user {user_id} ({user['name']})")
        return {"success": True, "message": f"Photo approved for {user['name']}!"}

    elif body.action == "reject":
        execute_query(
            """
            UPDATE users 
            SET pending_photo = NULL, 
                photo_status = 'rejected',
                photo_reject_reason = %s
            WHERE id = %s
            """,
            (body.reason or "Photo not appropriate", user_id)
        )
        logger.info(f"❌ Admin rejected photo for user {user_id} ({user['name']}): {body.reason}")
        return {"success": True, "message": f"Photo rejected for {user['name']}"}

    raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")


# ── Content Moderation (COMMENTED OUT — re-enable later) ──

# @router.get("/content")
# def get_all_content(
#     status: str = "all",
#     current_user: dict = Depends(require_admin)
# ):
#     condition = ""
#     params = []
#     if status != "all":
#         condition = "WHERE c.status = %s"
#         params.append(status)
#     content = execute_query(
#         f"""
#         SELECT c.id, c.title, c.description, c.type AS content_type,
#                c.price, c.is_free, c.thumbnail, c.status, c.created_at,
#                cf.file_url,
#                u.name AS creator_name, u.id AS creator_id
#         FROM content c
#         LEFT JOIN content_files cf ON c.id = cf.content_id AND cf.file_order = 0
#         JOIN users u ON u.id = c.creator_id
#         {condition}
#         ORDER BY c.created_at DESC
#         """,
#         tuple(params) if params else None,
#         fetch_all=True
#     )
#     return {"success": True, "data": content or []}


# @router.put("/content/{content_id}")
# def moderate_content(
#     content_id: int,
#     body: ContentActionRequest,
#     current_user: dict = Depends(require_admin)
# ):
#     content = execute_query(
#         "SELECT * FROM content WHERE id = %s",
#         (content_id,),
#         fetch_one=True
#     )
#     if not content:
#         raise HTTPException(status_code=404, detail="Content not found")
#     execute_query(
#         "UPDATE content SET status = %s WHERE id = %s",
#         (body.action, content_id)
#     )
#     return {"success": True, "message": f"Content {body.action}"}