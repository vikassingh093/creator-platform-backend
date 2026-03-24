from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
from typing import List
import logging
import os
import shutil
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["Content"])


# ── Local helpers ──────────────────────────────────────────
def require_creator(current_user: dict = Depends(get_current_user)):
    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Creator access required")
    return current_user


async def save_file(file: UploadFile, folder: str = "photos") -> str:
    ext = file.filename.split(".")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    path = f"uploads/{folder}"
    os.makedirs(path, exist_ok=True)
    file_path = f"{path}/{filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return file_path


# ── Routes ─────────────────────────────────────────────────
@router.get("/creator/{creator_id}")
def get_creator_content(
    creator_id: int,
    current_user: dict = Depends(get_current_user)
):
    profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (creator_id,),
        fetch_one=True
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator not found")

    content_list = execute_query(
        """
        SELECT c.id, c.title, c.type, c.price, c.is_free,
               c.duration, c.thumbnail, c.created_at,
               CASE WHEN cp.id IS NOT NULL THEN TRUE ELSE FALSE END as is_purchased
        FROM content c
        LEFT JOIN content_purchases cp
            ON cp.content_id = c.id AND cp.user_id = %s
        WHERE c.creator_id = %s
        ORDER BY c.created_at DESC
        """,
        (current_user["id"], profile["id"]),
        fetch_all=True
    )

    for item in content_list:
        files = execute_query(
            "SELECT file_url FROM content_files WHERE content_id = %s ORDER BY file_order ASC",
            (item["id"],),
            fetch_all=True
        )
        item["files"] = [f["file_url"] for f in files] if files else []

    return {"success": True, "data": content_list or []}


@router.post("/upload")
async def upload_content(
    title: str = Form(...),
    type: str = Form(...),
    price: float = Form(...),
    is_free: bool = Form(False),
    duration: str = Form(None),
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(require_creator)
):
    if type == "photo_pack" and len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 photos per pack")

    profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator profile not found")

    folder = "videos" if type == "video" else "photos"
    thumbnail = await save_file(files[0], folder=folder)

    content_id = execute_query(
        """
        INSERT INTO content (creator_id, title, type, price, is_free, duration, thumbnail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (profile["id"], title, type, price, is_free, duration, thumbnail),
        last_row_id=True
    )

    for idx, file in enumerate(files):
        file_url = thumbnail if idx == 0 else await save_file(file, folder=folder)
        execute_query(
            "INSERT INTO content_files (content_id, file_url, file_order) VALUES (%s, %s, %s)",
            (content_id, file_url, idx)
        )

    return {"success": True, "message": "Content uploaded successfully!", "content_id": content_id}


@router.post("/{content_id}/purchase")
def purchase_content(
    content_id: int,
    current_user: dict = Depends(get_current_user)
):
    content = execute_query(
        "SELECT * FROM content WHERE id = %s",
        (content_id,),
        fetch_one=True
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    if content["is_free"]:
        return {"success": True, "message": "This content is free!"}

    already = execute_query(
        "SELECT id FROM content_purchases WHERE user_id = %s AND content_id = %s",
        (current_user["id"], content_id),
        fetch_one=True
    )
    if already:
        raise HTTPException(status_code=400, detail="Already purchased")

    wallet = execute_query(
        "SELECT balance FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not wallet or float(wallet["balance"]) < float(content["price"]):
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")

    from app.routers.creators import get_creator_share
    price = float(content["price"])
    creator_amount, commission = get_creator_share(price)

    # ── Deduct from user ─────────────────────────────────────
    execute_query(
        "UPDATE wallets SET balance = balance - %s WHERE user_id = %s",
        (price, current_user["id"])
    )

    # ── Record purchase ──────────────────────────────────────
    execute_query(
        "INSERT INTO content_purchases (user_id, content_id, amount_paid) VALUES (%s, %s, %s)",
        (current_user["id"], content_id, price)
    )

    # ── Credit creator wallet ────────────────────────────────
    execute_query(
        """
        INSERT INTO creator_wallet (creator_id, balance, total_earned)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            balance = balance + VALUES(balance),
            total_earned = total_earned + VALUES(total_earned)
        """,
        (content["creator_id"], creator_amount, creator_amount)
    )

    # ── Log transaction ──────────────────────────────────────
    execute_query(
        """
        INSERT INTO transactions
            (user_id, creator_id, type, amount, creator_amount, commission_amount, description, status)
        VALUES (%s, %s, 'content', %s, %s, %s, %s, 'success')
        """,
        (
            current_user["id"],
            content["creator_id"],
            price,
            creator_amount,
            commission,
            f"Purchased content: {content['title']}"
        )
    )

    return {"success": True, "message": "Content unlocked successfully!"}


@router.delete("/{content_id}")
def delete_content(
    content_id: int,
    current_user: dict = Depends(require_creator)
):
    profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    content = execute_query(
        "SELECT * FROM content WHERE id = %s AND creator_id = %s",
        (content_id, profile["id"]),
        fetch_one=True
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    execute_query("DELETE FROM content WHERE id = %s", (content_id,))
    return {"success": True, "message": "Content deleted"}