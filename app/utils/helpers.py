import os

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

def full_image_url(path: str) -> str:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    path = path.lstrip("/")
    return f"{BASE_URL}/{path}"

def fix_photos(data: dict) -> dict:
    """Fix all photo fields in a dict"""
    if not data:
        return data
    photo_fields = ["profile_photo", "user_photo", "creator_photo", "sender_photo"]
    for field in photo_fields:
        if field in data and data[field]:
            data[field] = full_image_url(data[field])
    return data