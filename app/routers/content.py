from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import List
from app.middleware.auth_middleware import get_current_user, require_creator
from app.database import execute_query
from app.services.file_service import save_file
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["Content"])

@router.get("/creator/{creator_id}")
def get_creator_content(
    creator_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get all content for a creator with purchase status"""
    # Get creator profile id
    profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (creator_id,),
        fetch_one=True
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator not found")

    # Get content
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

    # Get files for each content
    for item in content_list:
        files = execute_query(
            """
            SELECT file_url FROM content_files
            WHERE content_id = %s ORDER BY file_order ASC
            """,
            (item["id"],),
            fetch_all=True
        )
        item["files"] = [f["file_url"] for f in files]

    return {"success": True, "data": content_list}

@router.post("/upload")
async def upload_content(
    title: str = Form(...),
    type: str = Form(...),       # photo | photo_pack | video
    price: float = Form(...),
    is_free: bool = Form(False),
    duration: str = Form(None),
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(require_creator)
):
    """Creator uploads content"""
    if type == "photo_pack" and len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 photos per pack")

    # Get creator profile
    profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator profile not found")

    # Save thumbnail (first file)
    folder = "videos" if type == "video" else "photos"
    thumbnail = await save_file(files[0], folder=folder)

    # Create content record
    content_id = execute_query(
        """
        INSERT INTO content (creator_id, title, type, price, is_free, duration, thumbnail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (profile["id"], title, type, price, is_free, duration, thumbnail),
        last_row_id=True
    )

    # Save all files
    for idx, file in enumerate(files):
        if idx == 0:
            file_url = thumbnail
        else:
            file_url = await save_file(file, folder=folder)

        execute_query(
            """
            INSERT INTO content_files (content_id, file_url, file_order)
            VALUES (%s, %s, %s)
            """,
            (content_id, file_url, idx)
        )

    return {
        "success": True,
        "message": "Content uploaded successfully!",
        "content_id": content_id
    }

@router.post("/{content_id}/purchase")
def purchase_content(
    content_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Purchase/unlock content"""
    # Check content exists
    content = execute_query(
        "SELECT * FROM content WHERE id = %s",
        (content_id,),
        fetch_one=True
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    if content["is_free"]:
        return {"success": True, "message": "This content is free!"}

    # Check already purchased
    already = execute_query(
        "SELECT id FROM content_purchases WHERE user_id = %s AND content_id = %s",
        (current_user["id"], content_id),
        fetch_one=True
    )
    if already:
        raise HTTPException(status_code=400, detail="Already purchased")

    # Check wallet balance
    wallet = execute_query(
        "SELECT balance FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    if not wallet or float(wallet["balance"]) < float(content["price"]):
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")

    # Deduct from wallet
    execute_query(
        """
        UPDATE wallets
        SET balance = balance - %s, total_spent = total_spent + %s
        WHERE user_id = %s
        """,
        (content["price"], content["price"], current_user["id"])
    )

    # Add to content purchases
    execute_query(
        """
        INSERT INTO content_purchases (user_id, content_id, amount_paid)
        VALUES (%s, %s, %s)
        """,
        (current_user["id"], content_id, content["price"])
    )

    # Add transaction record
    execute_query(
        """
        INSERT INTO transactions (user_id, type, amount, description, status)
        VALUES (%s, 'content', %s, %s, 'success')
        """,
        (current_user["id"], -content["price"], f"Purchased content: {content['title']}")
    )

    # Credit creator earnings
    execute_query(
        """
        UPDATE creator_profiles
        SET total_earnings = total_earnings + %s
        WHERE id = %s
        """,
        (content["price"], content["creator_id"])
    )

    return {"success": True, "message": "Content unlocked successfully!"}

@router.delete("/{content_id}")
def delete_content(
    content_id: int,
    current_user: dict = Depends(require_creator)
):
    """Delete content"""
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