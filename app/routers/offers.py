from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.middleware.auth_middleware import get_current_user
from app.helpers.offer_helper import validate_promo_code, get_active_offers_for_user
from app.helpers.settings_helper import is_offers_enabled

router = APIRouter(prefix="/offers", tags=["Offers"])


class ValidatePromoRequest(BaseModel):
    code: str
    amount: float


@router.get("/active")
def get_active_offers(current_user: dict = Depends(get_current_user)):
    """Get all available offers for the current user (shown on deposit page)."""
    if not is_offers_enabled():
        return {"success": True, "offers": [], "offers_enabled": False}

    offers = get_active_offers_for_user(current_user["id"])
    return {"success": True, "offers": offers, "offers_enabled": True}


@router.post("/validate-promo")
def validate_promo(body: ValidatePromoRequest, current_user: dict = Depends(get_current_user)):
    """Validate promo code before deposit — returns bonus preview."""
    result = validate_promo_code(current_user["id"], body.code, body.amount)
    return result