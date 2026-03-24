from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.middleware.auth_middleware import require_admin
from app.database import execute_query
import logging
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])

class ApproveCreatorRequest(BaseModel):
    action: str  # approve | reject
    note: str = None

class BlockUserRequest(BaseModel):
    is_blocked: bool

class WithdrawalActionRequest(BaseModel):
    action: str  # approved | rejected
    note: Optional[str] = None

class ContentActionRequest(BaseModel):
    action: str  # approved | rejected
    note: Optional[str] = None

class CommissionUpdateRequest(BaseModel):
    commission_percent: float

@router.get("/stats")
def get_stats(current_user: dict = Depends(require_admin)):
    """Get platform stats"""
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
    total_revenue = execute_query(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = 'add_money' AND status = 'success'",
        fetch_one=True
    )
    pending_withdrawals = execute_query(
        "SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM withdrawal_requests WHERE status = 'pending'",
        fetch_one=True
    )
    pending_content = execute_query(
        "SELECT COUNT(*) as count FROM content WHERE status = 'pending'",
        fetch_one=True
    )

    return {
        "success": True,
        "data": {
            "total_users": total_users["count"],
            "total_creators": total_creators["count"],
            "pending_approvals": pending_creators["count"],
            "total_revenue": float(total_revenue["total"]),
            "pending_withdrawals_count": pending_withdrawals["count"],
            "pending_withdrawals_amount": float(pending_withdrawals["total"]),
            "pending_content": pending_content["count"],
        }
    }

@router.get("/creators")
def get_all_creators(
    status: str = "all",  # all | pending | approved | rejected
    current_user: dict = Depends(require_admin)
):
    """Get all creators with filters"""
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
    """Approve or reject creator"""
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
        return {"success": True, "message": "Creator approved!"}
    elif body.action == "reject":
        execute_query(
            "UPDATE creator_profiles SET is_approved = FALSE, is_rejected = TRUE WHERE id = %s",
            (creator_id,)
        )
        return {"success": True, "message": "Creator rejected!"}

    raise HTTPException(status_code=400, detail="Action must be approve or reject")

@router.get("/users")
def get_all_users(current_user: dict = Depends(require_admin)):
    """Get all users"""
    users = execute_query(
        """
        SELECT u.id, u.name, u.phone, u.user_type,
               u.is_blocked, u.created_at,
               w.balance as wallet_balance
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.id
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
    """Block or unblock user"""
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
               u.name AS creator_name, u.phone AS creator_phone, u.id AS creator_user_id
        FROM withdrawal_requests wr
        JOIN users u ON u.id = wr.creator_id
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

    execute_query(
        "UPDATE withdrawal_requests SET status = %s, admin_note = %s, updated_at = NOW() WHERE id = %s",
        (body.action, body.note, withdrawal_id)
    )

    if body.action == "approved":
        # Update total_withdrawn in creator wallet
        execute_query(
            "UPDATE creator_wallet SET total_withdrawn = total_withdrawn + %s WHERE creator_id = %s",
            (withdrawal["amount"], withdrawal["creator_id"])
        )
    elif body.action == "rejected":
        # Refund balance back
        execute_query(
            "UPDATE creator_wallet SET balance = balance + %s WHERE creator_id = %s",
            (withdrawal["amount"], withdrawal["creator_id"])
        )

    return {"success": True, "message": f"Withdrawal {body.action}"}

# ── Content Moderation ────────────────────────────────────

@router.get("/content")
def get_all_content(
    status: str = "all",
    current_user: dict = Depends(require_admin)
):
    condition = ""
    params = []
    if status != "all":
        condition = "WHERE c.status = %s"
        params.append(status)

    content = execute_query(
        f"""
        SELECT c.id, c.title, c.description, c.type AS content_type,
               c.price, c.is_free, c.thumbnail, c.status, c.created_at,
               cf.file_url,
               u.name AS creator_name, u.id AS creator_id
        FROM content c
        LEFT JOIN content_files cf ON c.id = cf.content_id AND cf.file_order = 0
        JOIN users u ON u.id = c.creator_id
        {condition}
        ORDER BY c.created_at DESC
        """,
        tuple(params) if params else None,
        fetch_all=True
    )
    return {"success": True, "data": content or []}

@router.put("/content/{content_id}")
def moderate_content(
    content_id: int,
    body: ContentActionRequest,
    current_user: dict = Depends(require_admin)
):
    content = execute_query(
        "SELECT * FROM content WHERE id = %s",
        (content_id,),
        fetch_one=True
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    execute_query(
        "UPDATE content SET status = %s WHERE id = %s",
        (body.action, content_id)
    )
    return {"success": True, "message": f"Content {body.action}"}