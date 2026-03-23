from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from app.middleware.auth_middleware import get_current_user
from app.database import execute_query
from app.services.file_service import save_file
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])

class UpdateProfileRequest(BaseModel):
    name: str = None
    email: str = None

@router.get("/me")
def get_my_profile(current_user: dict = Depends(get_current_user)):
    """Get current user profile"""
    user = execute_query(
        """
        SELECT u.id, u.name, u.phone, u.email, u.profile_photo, u.user_type,
               w.balance as wallet_balance
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.id
        WHERE u.id = %s
        """,
        (current_user["id"],),
        fetch_one=True
    )
    return {"success": True, "data": user}

@router.put("/me")
def update_profile(
    body: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update user profile"""
    fields = []
    values = []

    if body.name:
        fields.append("name = %s")
        values.append(body.name)
    if body.email:
        fields.append("email = %s")
        values.append(body.email)

    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")

    values.append(current_user["id"])
    execute_query(
        f"UPDATE users SET {', '.join(fields)} WHERE id = %s",
        tuple(values)
    )
    return {"success": True, "message": "Profile updated successfully"}

@router.post("/me/photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload profile photo"""
    url = await save_file(file, folder="profiles")
    execute_query(
        "UPDATE users SET profile_photo = %s WHERE id = %s",
        (url, current_user["id"])
    )
    return {"success": True, "photo_url": url}

@router.get("/me/transactions")
def get_my_transactions(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    limit: int = 20
):
    """Get user transaction history"""
    offset = (page - 1) * limit
    transactions = execute_query(
        """
        SELECT id, type, amount, description, status, created_at
        FROM transactions
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        (current_user["id"], limit, offset),
        fetch_all=True
    )
    total = execute_query(
        "SELECT COUNT(*) as count FROM transactions WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    return {
        "success": True,
        "data": transactions,
        "total": total["count"],
        "page": page,
        "limit": limit
    }