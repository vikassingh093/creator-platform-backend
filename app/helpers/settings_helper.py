from app.database import execute_query


def get_setting(key: str, default: str = "") -> str:
    """Get a platform setting value by key."""
    row = execute_query(
        "SELECT setting_value FROM platform_settings WHERE setting_key = %s",
        (key,), fetch_one=True
    )
    return row["setting_value"] if row else default


def set_setting(key: str, value: str, description: str = None):
    """Set a platform setting value."""
    existing = execute_query(
        "SELECT id FROM platform_settings WHERE setting_key = %s",
        (key,), fetch_one=True
    )
    if existing:
        execute_query(
            "UPDATE platform_settings SET setting_value = %s WHERE setting_key = %s",
            (value, key)
        )
    else:
        execute_query(
            "INSERT INTO platform_settings (setting_key, setting_value, description) VALUES (%s, %s, %s)",
            (key, value, description)
        )


def is_offers_enabled() -> bool:
    """Check if the master offers toggle is ON."""
    return get_setting("offers_enabled", "0") == "1"