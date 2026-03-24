import os

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

def get_image_url(path: str) -> str:
    """Convert stored path to full URL"""
    if not path:
        return None
    # Already a full URL
    if path.startswith("http://") or path.startswith("https://"):
        return path
    # Remove leading slash if any
    path = path.lstrip("/")
    return f"{BASE_URL}/{path}"

def fix_user_photo(user: dict) -> dict:
    """Fix profile_photo field in any user/creator dict"""
    if not user:
        return user
    if "profile_photo" in user and user["profile_photo"]:
        user["profile_photo"] = get_image_url(user["profile_photo"])
    if "user_photo" in user and user["user_photo"]:
        user["user_photo"] = get_image_url(user["user_photo"])
    if "creator_photo" in user and user["creator_photo"]:
        user["creator_photo"] = get_image_url(user["creator_photo"])
    return user