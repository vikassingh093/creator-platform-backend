from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import execute_query
from app.services.jwt_service import verify_token

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub") or payload.get("user_id") or payload.get("id")
    user = execute_query(
        "SELECT id, name, phone, email, profile_photo, user_type, is_active, is_blocked FROM users WHERE id = %s",
        (int(user_id),),
        fetch_one=True
    )

    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user["is_blocked"]:
        raise HTTPException(status_code=403, detail="Account is blocked")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is inactive")

    return user

def get_admin_user(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

def get_creator_user(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")
    return current_user