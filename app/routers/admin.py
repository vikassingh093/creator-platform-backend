from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.middleware.auth_middleware import require_admin
from app.database import execute_query
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])

class ApproveCreatorRequest(BaseModel):
    action: str  # approve | reject
    note: str = None

class BlockUserRequest(BaseModel):
    is_blocked: bool

class PayoutActionRequest(BaseModel):
    action: str  # paid | rejected
    note: str = None

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
    pending_payouts = execute_query(
        "SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM payout_requests WHERE status = 'pending'",
        fetch_one=True
    )

    return {
        "success": True,
        "data": {
            "total_users": total_users["count"],
            "total_creators": total_creators["count"],
            "pending_approvals": pending_creators["count"],
            "total_revenue": float(total_revenue["total"]),
            "pending_payouts_count": pending_payouts["count"],
            "pending_payouts_amount": float(pending_payouts["total"])
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
            """
            UPDATE creator_profiles SET is_approved = TRUE, is_rejected = FALSE
            WHERE id = %s
            """,
            (creator_id,)
        )
        # Update user type to creator
        execute_query(
            """
            UPDATE users u
            JOIN creator_profiles cp ON cp.user_id = u.id
            SET u.user_type = 'creator'
            WHERE cp.id = %s
            """,
            (creator_id,)
        )
        return {"success": True, "message": "Creator approved!"}

    elif body.action == "reject":
        execute_query(
            """
            UPDATE creator_profiles SET is_approved = FALSE, is_rejected = TRUE
            WHERE id = %s
            """,
            (creator_id,)
        )
        return {"success": True, "message": "Creator rejected!"}

    raise HTTPException(status_code=400, detail="Action must be approve or reject")

@router.get("/users")
def get_all_users(current_user: dict = Depends(require_admin)):
    """Get all users"""
    users = execute_query(
        """
        SELECT u.id, u.name, u.phone, u.email, u.user_type,
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

@router.get("/payouts")
def get_payout_requests(current_user: dict = Depends(require_admin)):
    """Get all payout requests"""
    payouts = execute_query(
        """
        SELECT pr.id, pr.amount, pr.upi_id, pr.status,
               pr.requested_at, pr.processed_at, pr.admin_note,
               u.name as creator_name, u.phone as creator_phone
        FROM payout_requests pr
        JOIN creator_profiles cp ON cp.id = pr.creator_id
        JOIN users u ON u.id = cp.user_id
        ORDER BY pr.requested_at DESC
        """,
        fetch_all=True
    )
    return {"success": True, "data": payouts}

@router.put("/payouts/{payout_id}")
def process_payout(
    payout_id: int,
    body: PayoutActionRequest,
    current_user: dict = Depends(require_admin)
):
    """Process payout request"""
    payout = execute_query(
        "SELECT * FROM payout_requests WHERE id = %s",
        (payout_id,),
        fetch_one=True
    )
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")
    if payout["status"] != "pending":
        raise HTTPException(status_code=400, detail="Payout already processed")

    execute_query(
        """
        UPDATE payout_requests
        SET status = %s, admin_note = %s, processed_at = NOW()
        WHERE id = %s
        """,
        (body.action, body.note, payout_id)
    )
    return {"success": True, "message": f"Payout marked as {body.action}"}