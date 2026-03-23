from fastapi import UploadFile, HTTPException
from app.config import settings
import aiofiles
import os
import uuid
import logging

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/jpg", "image/webp"]
ALLOWED_VIDEO_TYPES = ["video/mp4", "video/quicktime", "video/x-msvideo"]

async def save_file(file: UploadFile, folder: str = "photos") -> str:
    """Save uploaded file and return URL"""
    # Check file size
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds {settings.MAX_FILE_SIZE_MB}MB limit"
        )

    # Check file type
    if folder == "videos":
        if file.content_type not in ALLOWED_VIDEO_TYPES:
            raise HTTPException(status_code=400, detail="Invalid video format. Use MP4.")
    else:
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image format. Use JPG/PNG.")

    # Generate unique filename
    ext = file.filename.split(".")[-1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, folder, filename)

    # Save file
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(contents)

    url = f"/uploads/{folder}/{filename}"
    logger.info(f"File saved: {url}")
    return url