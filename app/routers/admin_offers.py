from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from app.middleware.auth_middleware import require_admin
from app.database import execute_query
from app.helpers.settings_helper import get_setting, set_setting
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/offers", tags=["Admin Offers"])


# ── Models ──

class OfferCreateRequest(BaseModel):
    offer_type: str
    title: str
    description: Optional[str] = None
    bonus_type: str = "flat"
    bonus_value: float
    max_bonus_amount: Optional[float] = None
    min_deposit: float = 0
    event_name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: bool = True

class OfferUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    bonus_type: Optional[str] = None
    bonus_value: Optional[float] = None
    max_bonus_amount: Optional[float] = None
    min_deposit: Optional[float] = None
    event_name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: Optional[bool] = None

class PromoCreateRequest(BaseModel):
    code: str
    bonus_amount: float
    min_deposit: float = 0
    max_uses: Optional[int] = None
    max_per_user: int = 1
    event_name: Optional[str] = None
    expiry_date: Optional[str] = None
    is_active: bool = True

class PromoUpdateRequest(BaseModel):
    bonus_amount: Optional[float] = None
    min_deposit: Optional[float] = None
    max_uses: Optional[int] = None
    max_per_user: Optional[int] = None
    event_name: Optional[str] = None
    expiry_date: Optional[str] = None
    is_active: Optional[bool] = None

class ToggleRequest(BaseModel):
    enabled: bool


# ── Master Toggle ──

@router.get("/settings")
def get_offer_settings(current_user: dict = Depends(require_admin)):
    return {
        "success": True,
        "offers_enabled": get_setting("offers_enabled", "0") == "1"
    }

@router.put("/settings/toggle")
def toggle_offers_system(body: ToggleRequest, current_user: dict = Depends(require_admin)):
    set_setting("offers_enabled", "1" if body.enabled else "0", "Master toggle for offers/promo system")
    status = "enabled" if body.enabled else "disabled"
    logger.info(f"🔧 Offers system {status} by admin")
    return {"success": True, "message": f"Offers system {status}", "offers_enabled": body.enabled}


# ── Offers CRUD ──

@router.get("/")
def list_offers(current_user: dict = Depends(require_admin)):
    offers = execute_query(
        """SELECT o.*, 
            (SELECT COUNT(*) FROM offer_claims oc WHERE oc.offer_id = o.id) as total_claims,
            (SELECT COALESCE(SUM(oc.bonus_amount), 0) FROM offer_claims oc WHERE oc.offer_id = o.id) as total_bonus_given
        FROM offers o ORDER BY o.created_at DESC""",
        fetch_all=True
    )
    return {"success": True, "data": offers or []}

@router.post("/")
def create_offer(body: OfferCreateRequest, current_user: dict = Depends(require_admin)):
    if body.offer_type not in ("signup_bonus", "first_deposit", "event"):
        raise HTTPException(status_code=400, detail="Invalid offer type. Use: signup_bonus, first_deposit, event")

    if body.offer_type in ("signup_bonus", "first_deposit"):
        existing = execute_query(
            "SELECT id FROM offers WHERE offer_type = %s AND is_active = 1",
            (body.offer_type,), fetch_one=True
        )
        if existing:
            execute_query("UPDATE offers SET is_active = 0 WHERE id = %s", (existing["id"],))

    if body.offer_type == "event" and body.bonus_type != "percentage":
        raise HTTPException(status_code=400, detail="Event offers must use percentage bonus type")

    if body.offer_type in ("signup_bonus", "first_deposit") and body.bonus_type != "flat":
        raise HTTPException(status_code=400, detail="Signup/First deposit must use flat bonus type")

    if body.offer_type == "event" and (not body.start_date or not body.end_date):
        raise HTTPException(status_code=400, detail="Event offers require start_date and end_date")

    execute_query(
        """INSERT INTO offers 
        (offer_type, title, description, bonus_type, bonus_value, max_bonus_amount, 
         min_deposit, event_name, start_date, end_date, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (body.offer_type, body.title, body.description, body.bonus_type,
         body.bonus_value, body.max_bonus_amount, body.min_deposit,
         body.event_name, body.start_date, body.end_date, body.is_active)
    )
    logger.info(f"✅ Offer created: {body.offer_type} — {body.title}")
    return {"success": True, "message": "Offer created"}

@router.put("/{offer_id}")
def update_offer(offer_id: int, body: OfferUpdateRequest, current_user: dict = Depends(require_admin)):
    offer = execute_query("SELECT * FROM offers WHERE id = %s", (offer_id,), fetch_one=True)
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    updates = []
    params = []
    for field in ["title", "description", "bonus_type", "bonus_value", "max_bonus_amount",
                   "min_deposit", "event_name", "start_date", "end_date", "is_active"]:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    if updates:
        params.append(offer_id)
        execute_query(f"UPDATE offers SET {', '.join(updates)} WHERE id = %s", tuple(params))

    return {"success": True, "message": "Offer updated"}

@router.delete("/{offer_id}")
def delete_offer(offer_id: int, current_user: dict = Depends(require_admin)):
    execute_query("DELETE FROM offers WHERE id = %s", (offer_id,))
    return {"success": True, "message": "Offer deleted"}

@router.put("/{offer_id}/toggle")
def toggle_offer(offer_id: int, body: ToggleRequest, current_user: dict = Depends(require_admin)):
    offer = execute_query("SELECT * FROM offers WHERE id = %s", (offer_id,), fetch_one=True)
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    # For signup/first_deposit, deactivate others of same type when enabling
    if body.enabled and offer["offer_type"] in ("signup_bonus", "first_deposit"):
        execute_query(
            "UPDATE offers SET is_active = 0 WHERE offer_type = %s AND id != %s",
            (offer["offer_type"], offer_id)
        )

    execute_query("UPDATE offers SET is_active = %s WHERE id = %s", (body.enabled, offer_id))
    return {"success": True, "message": f"Offer {'enabled' if body.enabled else 'disabled'}"}


# ── Promo Codes CRUD ──

@router.get("/promos")
def list_promos(current_user: dict = Depends(require_admin)):
    promos = execute_query(
        """SELECT p.*,
            (SELECT COUNT(*) FROM offer_claims oc WHERE oc.promo_code_id = p.id) as total_claims,
            (SELECT COALESCE(SUM(oc.bonus_amount), 0) FROM offer_claims oc WHERE oc.promo_code_id = p.id) as total_bonus_given
        FROM promo_codes p ORDER BY p.created_at DESC""",
        fetch_all=True
    )
    return {"success": True, "data": promos or []}

@router.post("/promos")
def create_promo(body: PromoCreateRequest, current_user: dict = Depends(require_admin)):
    code = body.code.strip().upper()
    existing = execute_query("SELECT id FROM promo_codes WHERE code = %s", (code,), fetch_one=True)
    if existing:
        raise HTTPException(status_code=400, detail="Promo code already exists")

    execute_query(
        """INSERT INTO promo_codes 
        (code, bonus_amount, min_deposit, max_uses, max_per_user, event_name, expiry_date, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (code, body.bonus_amount, body.min_deposit, body.max_uses,
         body.max_per_user, body.event_name, body.expiry_date, body.is_active)
    )
    logger.info(f"🎟️ Promo created: {code} — ₹{body.bonus_amount}")
    return {"success": True, "message": f"Promo code {code} created"}

@router.put("/promos/{promo_id}")
def update_promo(promo_id: int, body: PromoUpdateRequest, current_user: dict = Depends(require_admin)):
    promo = execute_query("SELECT * FROM promo_codes WHERE id = %s", (promo_id,), fetch_one=True)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo code not found")

    updates = []
    params = []
    for field in ["bonus_amount", "min_deposit", "max_uses", "max_per_user",
                   "event_name", "expiry_date", "is_active"]:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    if updates:
        params.append(promo_id)
        execute_query(f"UPDATE promo_codes SET {', '.join(updates)} WHERE id = %s", tuple(params))

    return {"success": True, "message": "Promo code updated"}

@router.delete("/promos/{promo_id}")
def delete_promo(promo_id: int, current_user: dict = Depends(require_admin)):
    execute_query("DELETE FROM promo_codes WHERE id = %s", (promo_id,))
    return {"success": True, "message": "Promo code deleted"}

@router.put("/promos/{promo_id}/toggle")
def toggle_promo(promo_id: int, body: ToggleRequest, current_user: dict = Depends(require_admin)):
    promo = execute_query("SELECT * FROM promo_codes WHERE id = %s", (promo_id,), fetch_one=True)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo code not found")
    execute_query("UPDATE promo_codes SET is_active = %s WHERE id = %s", (body.enabled, promo_id))
    return {"success": True, "message": f"Promo code {'enabled' if body.enabled else 'disabled'}"}


# ── Claims History ──

@router.get("/claims")
def list_claims(
    claim_type: Optional[str] = Query(None),
    limit: int = 50,
    current_user: dict = Depends(require_admin)
):
    condition = ""
    params = []
    if claim_type:
        condition = "WHERE oc.claim_type = %s"
        params.append(claim_type)

    params.append(min(limit, 500))

    claims = execute_query(
        f"""SELECT oc.*, u.name as user_name, u.phone as user_phone,
            o.title as offer_title, o.offer_type,
            p.code as promo_code
        FROM offer_claims oc
        JOIN users u ON u.id = oc.user_id
        LEFT JOIN offers o ON o.id = oc.offer_id
        LEFT JOIN promo_codes p ON p.id = oc.promo_code_id
        {condition}
        ORDER BY oc.created_at DESC
        LIMIT %s""",
        tuple(params),
        fetch_all=True
    )
    return {"success": True, "data": claims or []}