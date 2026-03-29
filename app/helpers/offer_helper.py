from app.database import execute_query
from app.helpers.wallet_helper import credit_wallet
from app.helpers.settings_helper import is_offers_enabled
from datetime import date
import logging

logger = logging.getLogger(__name__)


def check_and_apply_bonus(user_id: int, deposit_amount: float, deposit_txn_id: int, promo_code: str = None):
    """
    Called after a successful deposit.
    Checks master toggle first, then offers in priority order:
    Promo Code > First Deposit > Event > Signup
    Only ONE bonus applies per deposit.
    """
    
    # ── Master toggle check ──
    if not is_offers_enabled():
        logger.info(f"🚫 Offers system disabled — no bonus for user {user_id}")
        return None

    # ── Priority 1: Promo Code ──
    if promo_code and promo_code.strip():
        result = _apply_promo_code(user_id, deposit_amount, deposit_txn_id, promo_code.strip().upper())
        if result:
            return result

    # ── Priority 2: First Deposit Bonus ──
    result = _apply_first_deposit_bonus(user_id, deposit_amount, deposit_txn_id)
    if result:
        return result

    # ── Priority 3: Event Bonus ──
    result = _apply_event_bonus(user_id, deposit_amount, deposit_txn_id)
    if result:
        return result

    # ── Priority 4: Signup Bonus (on first deposit only) ──
    result = _apply_signup_bonus(user_id, deposit_amount, deposit_txn_id)
    if result:
        return result

    return None


def _apply_promo_code(user_id: int, deposit_amount: float, deposit_txn_id: int, code: str):
    """Validate and apply promo code — flat bonus."""
    promo = execute_query(
        "SELECT * FROM promo_codes WHERE code = %s AND is_active = 1",
        (code,), fetch_one=True
    )
    if not promo:
        return None

    if promo["expiry_date"] and date.today() > promo["expiry_date"]:
        return None

    if promo["max_uses"] and promo["used_count"] >= promo["max_uses"]:
        return None

    if deposit_amount < float(promo["min_deposit"]):
        return None

    user_usage = execute_query(
        "SELECT COUNT(*) as cnt FROM offer_claims WHERE user_id = %s AND promo_code_id = %s",
        (user_id, promo["id"]), fetch_one=True
    )
    if user_usage["cnt"] >= (promo["max_per_user"] or 1):
        return None

    bonus_amount = float(promo["bonus_amount"])
    if bonus_amount <= 0:
        return None

    credit_wallet(user_id, bonus_amount, update_total_added=False)

    bonus_txn_id = _record_bonus_transaction(
        user_id=user_id,
        amount=bonus_amount,
        txn_type="promo_bonus",
        description=f"Promo code: {code} — ₹{bonus_amount} bonus on ₹{deposit_amount} deposit"
    )

    execute_query(
        """INSERT INTO offer_claims 
        (user_id, promo_code_id, claim_type, deposit_amount, bonus_amount, 
         deposit_transaction_id, bonus_transaction_id, claimed_at)
        VALUES (%s, %s, 'promo_bonus', %s, %s, %s, %s, CURDATE())""",
        (user_id, promo["id"], deposit_amount, bonus_amount, deposit_txn_id, bonus_txn_id)
    )

    execute_query(
        "UPDATE promo_codes SET used_count = used_count + 1 WHERE id = %s",
        (promo["id"],)
    )

    logger.info(f"🎟️ Promo {code} applied: ₹{bonus_amount} bonus for user {user_id}")
    return {
        "type": "promo_bonus",
        "title": f"Promo Code Applied!",
        "code": code,
        "event_name": promo.get("event_name"),
        "bonus_amount": bonus_amount,
        "message": f"🎉 ₹{bonus_amount} bonus credited with code {code}!"
    }


def _apply_first_deposit_bonus(user_id: int, deposit_amount: float, deposit_txn_id: int):
    """Auto-apply first deposit bonus — flat amount. Only once per user."""
    offer = execute_query(
        "SELECT * FROM offers WHERE offer_type = 'first_deposit' AND is_active = 1 LIMIT 1",
        fetch_one=True
    )
    if not offer:
        return None

    if deposit_amount < float(offer["min_deposit"]):
        return None

    already_claimed = execute_query(
        "SELECT id FROM offer_claims WHERE user_id = %s AND claim_type = 'first_deposit'",
        (user_id,), fetch_one=True
    )
    if already_claimed:
        return None

    deposit_count = execute_query(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id = %s AND type = 'add_money' AND status = 'success'",
        (user_id,), fetch_one=True
    )
    if deposit_count["cnt"] > 1:
        return None

    bonus_amount = float(offer["bonus_value"])
    if bonus_amount <= 0:
        return None

    credit_wallet(user_id, bonus_amount, update_total_added=False)

    bonus_txn_id = _record_bonus_transaction(
        user_id=user_id,
        amount=bonus_amount,
        txn_type="first_deposit_bonus",
        description=f"First deposit bonus — ₹{bonus_amount} on ₹{deposit_amount} deposit"
    )

    execute_query(
        """INSERT INTO offer_claims 
        (user_id, offer_id, claim_type, deposit_amount, bonus_amount, 
         deposit_transaction_id, bonus_transaction_id, claimed_at)
        VALUES (%s, %s, 'first_deposit', %s, %s, %s, %s, CURDATE())""",
        (user_id, offer["id"], deposit_amount, bonus_amount, deposit_txn_id, bonus_txn_id)
    )

    logger.info(f"🎁 First deposit bonus: ₹{bonus_amount} for user {user_id}")
    return {
        "type": "first_deposit_bonus",
        "title": "First Deposit Bonus!",
        "bonus_amount": bonus_amount,
        "message": f"🎁 ₹{bonus_amount} first deposit bonus credited!"
    }


def _apply_event_bonus(user_id: int, deposit_amount: float, deposit_txn_id: int):
    """Auto-apply active event bonus — PERCENTAGE based. Once per day per user."""
    today = date.today()
    event = execute_query(
        """SELECT * FROM offers 
        WHERE offer_type = 'event' AND is_active = 1 
        AND start_date <= %s AND end_date >= %s
        ORDER BY bonus_value DESC LIMIT 1""",
        (today, today), fetch_one=True
    )
    if not event:
        return None

    if deposit_amount < float(event["min_deposit"]):
        return None

    already_today = execute_query(
        """SELECT id FROM offer_claims 
        WHERE user_id = %s AND offer_id = %s AND claimed_at = CURDATE()""",
        (user_id, event["id"]), fetch_one=True
    )
    if already_today:
        return None

    percentage = float(event["bonus_value"])
    bonus_amount = round(deposit_amount * percentage / 100, 2)

    if event["max_bonus_amount"] and bonus_amount > float(event["max_bonus_amount"]):
        bonus_amount = float(event["max_bonus_amount"])

    if bonus_amount <= 0:
        return None

    credit_wallet(user_id, bonus_amount, update_total_added=False)

    bonus_txn_id = _record_bonus_transaction(
        user_id=user_id,
        amount=bonus_amount,
        txn_type="event_bonus",
        description=f"{event['event_name'] or event['title']} — {percentage}% bonus (₹{bonus_amount}) on ₹{deposit_amount} deposit"
    )

    execute_query(
        """INSERT INTO offer_claims 
        (user_id, offer_id, claim_type, deposit_amount, bonus_amount, 
         deposit_transaction_id, bonus_transaction_id, claimed_at)
        VALUES (%s, %s, 'event_bonus', %s, %s, %s, %s, CURDATE())""",
        (user_id, event["id"], deposit_amount, bonus_amount, deposit_txn_id, bonus_txn_id)
    )

    logger.info(f"🎄 Event bonus ({event['event_name']}): ₹{bonus_amount} ({percentage}%) for user {user_id}")
    return {
        "type": "event_bonus",
        "title": f"{event['event_name'] or event['title']}!",
        "event_name": event.get("event_name"),
        "percentage": percentage,
        "bonus_amount": bonus_amount,
        "message": f"🎉 {event['event_name'] or 'Event'} bonus! {percentage}% extra = ₹{bonus_amount} credited!"
    }


def _apply_signup_bonus(user_id: int, deposit_amount: float, deposit_txn_id: int):
    """Auto-apply signup bonus on first deposit — flat amount. Only once per user lifetime."""
    offer = execute_query(
        "SELECT * FROM offers WHERE offer_type = 'signup_bonus' AND is_active = 1 LIMIT 1",
        fetch_one=True
    )
    if not offer:
        return None

    if deposit_amount < float(offer["min_deposit"]):
        return None

    already_claimed = execute_query(
        "SELECT id FROM offer_claims WHERE user_id = %s AND claim_type = 'signup_bonus'",
        (user_id,), fetch_one=True
    )
    if already_claimed:
        return None

    deposit_count = execute_query(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id = %s AND type = 'add_money' AND status = 'success'",
        (user_id,), fetch_one=True
    )
    if deposit_count["cnt"] > 1:
        return None

    bonus_amount = float(offer["bonus_value"])
    if bonus_amount <= 0:
        return None

    credit_wallet(user_id, bonus_amount, update_total_added=False)

    bonus_txn_id = _record_bonus_transaction(
        user_id=user_id,
        amount=bonus_amount,
        txn_type="signup_bonus",
        description=f"Welcome signup bonus — ₹{bonus_amount}"
    )

    execute_query(
        """INSERT INTO offer_claims 
        (user_id, offer_id, claim_type, deposit_amount, bonus_amount, 
         deposit_transaction_id, bonus_transaction_id, claimed_at)
        VALUES (%s, %s, 'signup_bonus', %s, %s, %s, %s, CURDATE())""",
        (user_id, offer["id"], deposit_amount, bonus_amount, deposit_txn_id, bonus_txn_id)
    )

    logger.info(f"🎉 Signup bonus: ₹{bonus_amount} for user {user_id}")
    return {
        "type": "signup_bonus",
        "title": "Welcome Bonus!",
        "bonus_amount": bonus_amount,
        "message": f"🎉 Welcome! ₹{bonus_amount} signup bonus credited!"
    }


def _record_bonus_transaction(user_id: int, amount: float, txn_type: str, description: str):
    """Create a separate transaction entry for the bonus."""
    execute_query(
        """INSERT INTO transactions 
        (user_id, type, amount, description, status, created_at)
        VALUES (%s, %s, %s, %s, 'success', NOW())""",
        (user_id, txn_type, amount, description)
    )
    txn = execute_query("SELECT LAST_INSERT_ID() as id", fetch_one=True)
    return txn["id"] if txn else None


def validate_promo_code(user_id: int, code: str, deposit_amount: float):
    """Validate promo code before deposit (for UI preview). Does NOT apply it."""
    if not is_offers_enabled():
        return {"valid": False, "error": "Offers are currently disabled"}

    code = code.strip().upper()
    promo = execute_query(
        "SELECT * FROM promo_codes WHERE code = %s AND is_active = 1",
        (code,), fetch_one=True
    )
    if not promo:
        return {"valid": False, "error": "Invalid promo code"}

    if promo["expiry_date"] and date.today() > promo["expiry_date"]:
        return {"valid": False, "error": "Promo code has expired"}

    if promo["max_uses"] and promo["used_count"] >= promo["max_uses"]:
        return {"valid": False, "error": "Promo code fully redeemed"}

    if deposit_amount < float(promo["min_deposit"]):
        return {"valid": False, "error": f"Minimum deposit ₹{int(promo['min_deposit'])} required"}

    user_usage = execute_query(
        "SELECT COUNT(*) as cnt FROM offer_claims WHERE user_id = %s AND promo_code_id = %s",
        (user_id, promo["id"]), fetch_one=True
    )
    if user_usage["cnt"] >= (promo["max_per_user"] or 1):
        return {"valid": False, "error": "You have already used this code"}

    return {
        "valid": True,
        "bonus_amount": float(promo["bonus_amount"]),
        "event_name": promo.get("event_name"),
        "message": f"🎉 You'll get ₹{int(promo['bonus_amount'])} bonus!"
    }


def get_active_offers_for_user(user_id: int):
    """Return list of currently available offers for the user (for display on deposit page)."""
    if not is_offers_enabled():
        return []

    today = date.today()
    offers = []

    # Check signup bonus
    signup = execute_query(
        "SELECT * FROM offers WHERE offer_type = 'signup_bonus' AND is_active = 1 LIMIT 1",
        fetch_one=True
    )
    if signup:
        claimed = execute_query(
            "SELECT id FROM offer_claims WHERE user_id = %s AND claim_type = 'signup_bonus'",
            (user_id,), fetch_one=True
        )
        if not claimed:
            deposit_count = execute_query(
                "SELECT COUNT(*) as cnt FROM transactions WHERE user_id = %s AND type = 'add_money' AND status = 'success'",
                (user_id,), fetch_one=True
            )
            if deposit_count["cnt"] == 0:
                offers.append({
                    "type": "signup_bonus",
                    "title": signup["title"],
                    "description": signup["description"],
                    "bonus_text": f"₹{int(signup['bonus_value'])} bonus",
                    "min_deposit": float(signup["min_deposit"]),
                    "icon": "🎉"
                })

    # Check first deposit
    first_dep = execute_query(
        "SELECT * FROM offers WHERE offer_type = 'first_deposit' AND is_active = 1 LIMIT 1",
        fetch_one=True
    )
    if first_dep:
        claimed = execute_query(
            "SELECT id FROM offer_claims WHERE user_id = %s AND claim_type = 'first_deposit'",
            (user_id,), fetch_one=True
        )
        if not claimed:
            deposit_count = execute_query(
                "SELECT COUNT(*) as cnt FROM transactions WHERE user_id = %s AND type = 'add_money' AND status = 'success'",
                (user_id,), fetch_one=True
            )
            if deposit_count["cnt"] == 0:
                offers.append({
                    "type": "first_deposit",
                    "title": first_dep["title"],
                    "description": first_dep["description"],
                    "bonus_text": f"₹{int(first_dep['bonus_value'])} bonus",
                    "min_deposit": float(first_dep["min_deposit"]),
                    "icon": "🎁"
                })

    # Check active events
    events = execute_query(
        """SELECT * FROM offers 
        WHERE offer_type = 'event' AND is_active = 1 
        AND start_date <= %s AND end_date >= %s
        ORDER BY bonus_value DESC""",
        (today, today), fetch_all=True
    )
    for event in (events or []):
        already_today = execute_query(
            "SELECT id FROM offer_claims WHERE user_id = %s AND offer_id = %s AND claimed_at = CURDATE()",
            (user_id, event["id"]), fetch_one=True
        )
        if not already_today:
            max_text = f" (max ₹{int(event['max_bonus_amount'])})" if event["max_bonus_amount"] else ""
            offers.append({
                "type": "event",
                "title": event["event_name"] or event["title"],
                "description": event["description"],
                "bonus_text": f"{int(event['bonus_value'])}% bonus{max_text}",
                "min_deposit": float(event["min_deposit"]),
                "icon": "🎄"
            })

    return offers