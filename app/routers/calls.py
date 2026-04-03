from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
from app.config import (
    AUDIO_RATE,
    VIDEO_RATE,
    TICK_INTERVAL,
    CALL_RING_TIMEOUT_SECONDS,
    CALL_STUCK_TIMEOUT_MINUTES
)

# ✅ Import helpers — all money logic lives there
from app.helpers.wallet_helper import (
    debit_wallet, credit_creator_wallet, get_balance,
    has_sufficient_balance, ensure_wallet_exists
)
from app.helpers.transaction_helper import (
    record_call_transaction, record_refund, calculate_split
)

import logging
import os
import time
import math

try:
    from agora_token_builder import RtcTokenBuilder
    AGORA_AVAILABLE = True
    print("✅ agora_token_builder imported successfully")
except ImportError as e:
    AGORA_AVAILABLE = False
    RtcTokenBuilder = None
    print(f"❌ agora_token_builder import failed: {e}")

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calls", tags=["Calls"])


# ── Pydantic Models ─────────────────────────────────────────
class TickRequest(BaseModel):
    room_id: int

class EndCallRequest(BaseModel):
    room_id: int
    duration: int


# ── Helper ──────────────────────────────────────────────────
def generate_agora_token(channel_name: str, uid: int) -> str:
    app_id = os.getenv("AGORA_APP_ID", "")
    app_certificate = os.getenv("AGORA_APP_CERTIFICATE", "")

    if not AGORA_AVAILABLE or not app_certificate:
        return f"mock_token_{channel_name}_{uid}"

    try:
        expire_time = int(time.time()) + 3600
        token = RtcTokenBuilder.buildTokenWithUid(
            app_id, app_certificate, channel_name, uid, 1, expire_time
        )
        return token
    except Exception as e:
        logger.error(f"Agora token error: {e}")
        return f"mock_token_{channel_name}_{uid}"


# ── Routes ──────────────────────────────────────────────────

@router.post("/initiate")
async def initiate_call(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    print("=" * 60)
    print("📞 CALL INITIATE STARTED")
    print("=" * 60)

    body = await request.json()
    creator_id = body.get("creator_id")
    call_type = body.get("call_type")

    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")
    if call_type not in ["audio", "video"]:
        raise HTTPException(status_code=400, detail="Invalid call type")

    creator = execute_query(
        """
        SELECT cp.*, u.name, u.phone
        FROM creator_profiles cp
        JOIN users u ON u.id = cp.user_id
        WHERE cp.user_id = %s AND cp.is_approved = 1
        """,
        (creator_id,),
        fetch_one=True
    )

    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    if not creator.get("is_online"):
        raise HTTPException(status_code=400, detail="Creator is currently offline")

    rate = AUDIO_RATE if call_type == "audio" else VIDEO_RATE

    # ✅ Use helper to check balance
    balance = get_balance(current_user["id"])
    print(f"📌 Wallet balance: ₹{balance} | Required: ₹{rate}")

    if balance < rate:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Need at least ₹{rate} for 1 minute."
        )

    # Clean up stuck calls
    execute_query(
        f"""
        UPDATE call_rooms SET status='ended', ended_at=NOW()
        WHERE status='active' AND started_at < NOW() - INTERVAL {CALL_STUCK_TIMEOUT_MINUTES} MINUTE
        """
    )
    execute_query(
        "UPDATE call_rooms SET status='ended', ended_at=NOW() WHERE user_id=%s AND status='active'",
        (current_user["id"],)
    )

    channel_name = f"call_{current_user['id']}_{creator_id}_{int(time.time())}"
    app_id = os.getenv("AGORA_APP_ID", "")
    token = generate_agora_token(channel_name, uid=1)

    # In initiate_call — replace the INSERT + SELECT pattern:
    # 🔴 FIX #7: Use last_row_id to avoid race condition
    room_id = execute_query(
        """
        INSERT INTO call_rooms
        (user_id, creator_id, call_type, channel_name, status, started_at, initiated_by)
        VALUES (%s, %s, %s, %s, 'ringing', NOW(), 'customer')
        """,
        (current_user["id"], creator_id, call_type, channel_name),
        last_row_id=True
    )

    room = execute_query(
        "SELECT * FROM call_rooms WHERE id = %s",
        (room_id,),
        fetch_one=True
    )
    print(f"✅ Room created: {room['id']} | Channel: {channel_name}")

    return {
        "success": True,
        "room_id": room["id"],
        "channel_name": channel_name,
        "token": token,
        "uid": 1,
        "app_id": app_id,
        "call_type": call_type,
        "rate_per_minute": rate,
        "balance": balance,
        "creator": {
            "id": creator["user_id"],
            "name": creator["name"],
        }
    }


@router.post("/creator-initiate")
async def creator_initiate_call(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Creator initiates call to a customer — CUSTOMER PAYS"""
    print("=" * 60)
    print("📞 CREATOR-INITIATED CALL")
    print("=" * 60)

    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Only creators can use this endpoint")

    body = await request.json()
    customer_id = body.get("customer_id")
    call_type = body.get("call_type")

    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id is required")
    if call_type not in ["audio", "video"]:
        raise HTTPException(status_code=400, detail="Invalid call type")

    customer = execute_query(
        "SELECT id, name, phone FROM users WHERE id = %s AND user_type IN ('customer', 'user') AND is_active = 1",
        (customer_id,),
        fetch_one=True
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    creator_profile = execute_query(
        "SELECT * FROM creator_profiles WHERE user_id = %s AND is_approved = 1",
        (current_user["id"],),
        fetch_one=True
    )
    if not creator_profile:
        raise HTTPException(status_code=400, detail="Creator profile not found")

    rate = AUDIO_RATE if call_type == "audio" else VIDEO_RATE

    # ✅ Use helper to check CUSTOMER's balance
    balance = get_balance(customer_id)
    print(f"📌 Customer wallet: ₹{balance} | Required: ₹{rate}")

    if balance < rate:
        raise HTTPException(
            status_code=400,
            detail=f"Customer has insufficient balance (₹{balance:.2f}). Need ₹{rate}."
        )

    # Clean up stuck calls
    execute_query(
        f"""
        UPDATE call_rooms SET status='ended', ended_at=NOW()
        WHERE status='active' AND started_at < NOW() - INTERVAL {CALL_STUCK_TIMEOUT_MINUTES} MINUTE
        """
    )

    channel_name = f"call_{current_user['id']}_{customer_id}_{int(time.time())}"
    app_id = os.getenv("AGORA_APP_ID", "")
    creator_token = generate_agora_token(channel_name, uid=1)

    execute_query(
        """
        INSERT INTO call_rooms
        (user_id, creator_id, call_type, channel_name, status, started_at, initiated_by)
        VALUES (%s, %s, %s, %s, 'ringing', NOW(), 'creator')
        """,
        (customer_id, current_user["id"], call_type, channel_name)
    )

    room = execute_query(
        "SELECT * FROM call_rooms WHERE creator_id=%s ORDER BY id DESC LIMIT 1",
        (current_user["id"],),
        fetch_one=True
    )
    print(f"✅ Creator-initiated room: {room['id']} | Channel: {channel_name}")

    return {
        "success": True,
        "room_id": room["id"],
        "channel_name": channel_name,
        "token": creator_token,
        "uid": 1,
        "app_id": app_id,
        "call_type": call_type,
        "rate_per_minute": rate,
        "customer": {
            "id": customer["id"],
            "name": customer["name"],
        }
    }


@router.post("/tick")
def call_tick(
    body: TickRequest,
    current_user: dict = Depends(get_current_user)
):
    # ✅ Allow both customer AND creator to tick
    room = execute_query(
        "SELECT * FROM call_rooms WHERE id=%s AND (user_id=%s OR creator_id=%s) AND status='active'",
        (body.room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )

    if not room:
        return {"success": False, "should_end": True, "reason": "room_ended"}

    # ✅ Only deduct from the CUSTOMER (user_id), not creator
    is_payer = (current_user["id"] == room["user_id"])

    rate = AUDIO_RATE if room["call_type"] == "audio" else VIDEO_RATE
    tick_cost = round((rate / 60) * TICK_INTERVAL, 4)

    # ✅ Use helper to check CUSTOMER's balance
    balance = get_balance(room["user_id"])

    if balance < tick_cost:
        print(f"⚠️ Balance ₹{balance} < tick cost ₹{tick_cost} - ending call")
        execute_query(
            "UPDATE call_rooms SET status='ended', ended_at=NOW() WHERE id=%s",
            (body.room_id,)
        )
        return {
            "success": True,
            "should_end": True,
            "reason": "balance_exhausted",
            "balance": balance
        }

    # ✅ Only deduct if the CUSTOMER is the one ticking
    if is_payer:
        # ✅ Use helper — updates balance AND total_spent
        wallet = debit_wallet(room["user_id"], tick_cost)
        if wallet is None:
            # Race condition — balance dropped between check and debit
            execute_query(
                "UPDATE call_rooms SET status='ended', ended_at=NOW() WHERE id=%s",
                (body.room_id,)
            )
            return {
                "success": True,
                "should_end": True,
                "reason": "balance_exhausted",
                "balance": 0
            }

        # ✅ Update call_rooms total_cost
        execute_query(
            "UPDATE call_rooms SET total_cost = COALESCE(total_cost, 0) + %s WHERE id=%s",
            (tick_cost, body.room_id)
        )

        # ✅ Calculate split and credit creator using helper
        split = calculate_split(tick_cost)
        credit_creator_wallet(room["creator_id"], split["creator_amount"])

        new_balance = float(wallet["balance"])
        print(
            f"💰 Tick (customer): ₹{tick_cost} deducted | "
            f"Creator: +₹{split['creator_amount']} | Commission: ₹{split['commission_amount']} | "
            f"Balance: ₹{new_balance:.2f}"
        )
    else:
        new_balance = balance
        print(f"💰 Tick (creator): no deduction | Customer balance: ₹{new_balance:.2f}")

    is_low = new_balance < rate
    minutes_left = new_balance / rate if rate > 0 else 0

    return {
        "success": True,
        "should_end": False,
        "balance": round(new_balance, 2),
        "tick_cost": tick_cost if is_payer else 0,
        "low_balance": is_low,
        "minutes_left": round(minutes_left, 1),
        "reason": None
    }


@router.post("/end")
def end_call(
    body: EndCallRequest,
    current_user: dict = Depends(get_current_user)
):
    print("=" * 60)
    print(f"📞 END CALL - room_id={body.room_id}, duration={body.duration}")
    print("=" * 60)

    # ✅ Allow both customer AND creator to end
    room = execute_query(
        "SELECT * FROM call_rooms WHERE id=%s AND (user_id=%s OR creator_id=%s)",
        (body.room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )

    if not room:
        return {"success": True, "duration": 0, "total_cost": 0}

    # Already ended? Don't process again
    if room.get("status") == "ended":
        return {
            "success": True,
            "duration": room.get("duration", 0),
            "total_cost": float(room.get("total_cost", 0))
        }

    rate = AUDIO_RATE if room["call_type"] == "audio" else VIDEO_RATE
    total_cost = float(room.get("total_cost") or 0)

    # If total_cost is 0 but duration > 0, calculate from duration (fallback)
    if total_cost == 0 and body.duration > 0:
        total_cost = round((rate / 60) * body.duration, 2)

    # ── Duration = 0 means NOT ANSWERED ───────────────────────
    if body.duration == 0:
        print("⚠️ Duration=0 - not answered")

        # ✅ SCENARIO 3: Check if tick already deducted money (auto-refund)
        if total_cost > 0:
            print(f"⚠️ Tick deducted ₹{total_cost} but call not answered — AUTO REFUND")

            # Refund customer
            from app.helpers.wallet_helper import credit_wallet
            credit_wallet(room["user_id"], total_cost, update_total_added=False)

            # Reverse creator credit
            split = calculate_split(total_cost)
            from app.helpers.wallet_helper import debit_creator_wallet
            debit_creator_wallet(room["creator_id"], split["creator_amount"])

            # Record refund transaction
            record_refund(
                user_id=room["user_id"],
                amount=total_cost,
                reason=f"Call not answered - auto refund ({room['call_type']} call)",
                reference_id=f"call_{body.room_id}",
                creator_id=room["creator_id"]
            )

            print(f"✅ Auto refund ₹{total_cost} to user {room['user_id']}")

        execute_query(
            "UPDATE call_rooms SET status='ended', ended_at=NOW(), duration=0, total_cost=0 WHERE id=%s",
            (body.room_id,)
        )

        # Notifications — missed call
        try:
            execute_query(
                """
                INSERT INTO notifications (user_id, type, title, message, reference_id, is_read)
                VALUES (%s, 'call', %s, %s, %s, 0)
                """,
                (
                    room["user_id"],
                    "📞 Missed Call",
                    f"Your {room['call_type']} call to creator was not answered. Tap to call back.",
                    f"missed_call_{room['creator_id']}_{room['call_type']}"
                )
            )
        except Exception as e:
            print(f"⚠️ User notification error: {e}")

        try:
            caller = execute_query(
                "SELECT name FROM users WHERE id = %s",
                (room["user_id"],),
                fetch_one=True
            )
            caller_name = caller["name"] if caller else "A user"
            execute_query(
                """
                INSERT INTO notifications (user_id, type, title, message, reference_id, is_read)
                VALUES (%s, 'call', %s, %s, %s, 0)
                """,
                (
                    room["creator_id"],
                    "📵 Missed Call",
                    f"{caller_name} tried to {room['call_type']} call you but you were unavailable.",
                    f"creator_missed_call_{room['user_id']}_{room['call_type']}"
                )
            )
        except Exception as e:
            print(f"⚠️ Creator notification error: {e}")

        return {"success": True, "duration": 0, "total_cost": 0, "message": "Not answered - no charge"}

    # ── CALL WAS ANSWERED — record final transaction ──────────
    minutes = math.ceil(body.duration / 60)

    execute_query(
        """
        UPDATE call_rooms
        SET status='ended', ended_at=NOW(), duration=%s, total_cost=%s
        WHERE id=%s
        """,
        (body.duration, total_cost, body.room_id)
    )

    # ✅ Record ONE summary transaction with creator_id, creator_amount, commission
    existing_tx = execute_query(
        "SELECT id FROM transactions WHERE reference_id = %s LIMIT 1",
        (f"call_{body.room_id}",),
        fetch_one=True
    )
    if not existing_tx:
        try:
            record_call_transaction(
                user_id=room["user_id"],
                creator_id=room["creator_id"],
                call_type=room["call_type"],
                duration_seconds=body.duration,
                total_cost=total_cost,
                room_id=body.room_id
            )
            print(f"✅ Transaction saved: ₹{total_cost} (with creator split)")
        except Exception as e:
            print(f"⚠️ Transaction error: {e}")
            logger.error(f"Transaction recording failed: {e}", exc_info=True)
    else:
        print("⚠️ Transaction already exists - skipping duplicate")

    print(f"✅ Call ended - {body.duration}s | Cost: ₹{total_cost}")

    return {
        "success": True,
        "duration": body.duration,
        "minutes": minutes,
        "total_cost": total_cost,
        "rate_per_minute": rate
    }


@router.get("/incoming")
def get_incoming_calls(current_user: dict = Depends(get_current_user)):
    execute_query(
        f"""
        UPDATE call_rooms SET status='ended', ended_at=NOW()
        WHERE status='ringing' 
        AND started_at < NOW() - INTERVAL {CALL_RING_TIMEOUT_SECONDS} SECOND
        """
    )

    # ── Check 1: Customer receives call FROM creator
    call = execute_query(
        """
        SELECT cr.*, u.name as caller_name
        FROM call_rooms cr
        JOIN users u ON u.id = cr.creator_id
        WHERE cr.user_id = %s
        AND cr.status = 'ringing'
        AND cr.initiated_by = 'creator'
        AND cr.started_at >= NOW() - INTERVAL 30 SECOND
        ORDER BY cr.started_at DESC
        LIMIT 1
        """,
        (current_user["id"],),
        fetch_one=True
    )

    if call:
        app_id = os.getenv("AGORA_APP_ID", "")
        customer_token = generate_agora_token(call["channel_name"], uid=2)
        return {
            "call": {
                "room_id": call["id"],
                "channel_name": call["channel_name"],
                "call_type": call["call_type"],
                "caller_name": call["caller_name"],
                "app_id": app_id,
                "token": customer_token,
                "uid": 2,
                "initiated_by": "creator"
            }
        }

    # ── Check 2: Creator receives call FROM customer
    call = execute_query(
        """
        SELECT cr.*, u.name as caller_name
        FROM call_rooms cr
        JOIN users u ON u.id = cr.user_id
        WHERE cr.creator_id = %s
        AND cr.status = 'ringing'
        AND cr.started_at >= NOW() - INTERVAL 30 SECOND
        ORDER BY cr.started_at DESC
        LIMIT 1
        """,
        (current_user["id"],),
        fetch_one=True
    )

    if not call:
        return {"call": None}

    app_id = os.getenv("AGORA_APP_ID", "")
    creator_token = generate_agora_token(call["channel_name"], uid=2)

    return {
        "call": {
            "room_id": call["id"],
            "channel_name": call["channel_name"],
            "call_type": call["call_type"],
            "caller_name": call["caller_name"],
            "app_id": app_id,
            "token": creator_token,
            "uid": 2,
            "initiated_by": "customer"
        }
    }


@router.post("/reject/{room_id}")
def reject_call(room_id: int, current_user: dict = Depends(get_current_user)):
    """
    Reject a ringing call.
    ✅ Scenario 3: If tick already deducted money while ringing, auto-refund.
    """
    # Get room info BEFORE ending it
    room = execute_query(
        "SELECT * FROM call_rooms WHERE id=%s AND (creator_id=%s OR user_id=%s) AND status='ringing'",
        (room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )

    if room:
        total_cost = float(room.get("total_cost") or 0)

        # ✅ AUTO REFUND if tick deducted money during ringing
        if total_cost > 0:
            print(f"⚠️ Call {room_id} rejected but ₹{total_cost} was deducted — AUTO REFUND")

            from app.helpers.wallet_helper import credit_wallet
            credit_wallet(room["user_id"], total_cost, update_total_added=False)

            split = calculate_split(total_cost)
            from app.helpers.wallet_helper import debit_creator_wallet
            debit_creator_wallet(room["creator_id"], split["creator_amount"])

            record_refund(
                user_id=room["user_id"],
                amount=total_cost,
                reason=f"Call rejected - auto refund ({room['call_type']} call)",
                reference_id=f"call_{room_id}",
                creator_id=room["creator_id"]
            )

            print(f"✅ Auto refund ₹{total_cost} to user {room['user_id']}")

    execute_query(
        """
        UPDATE call_rooms SET status='ended', ended_at=NOW(), total_cost=0 
        WHERE id=%s AND (creator_id=%s OR user_id=%s) AND status='ringing'
        """,
        (room_id, current_user["id"], current_user["id"])
    )
    print(f"❌ Call {room_id} rejected by user {current_user['id']}")
    return {"success": True}


@router.post("/accept/{room_id}")
def accept_call(room_id: int, current_user: dict = Depends(get_current_user)):
    room = execute_query(
        "SELECT * FROM call_rooms WHERE id=%s AND (creator_id=%s OR user_id=%s)",
        (room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )
    if not room:
        raise HTTPException(status_code=404, detail="Call not found")

    execute_query(
        "UPDATE call_rooms SET status='active' WHERE id=%s",
        (room_id,)
    )
    print(f"✅ Call {room_id} accepted by user {current_user['id']}")
    return {"success": True, "status": "active"}


@router.get("/status/{room_id}")
def get_call_status(room_id: int, current_user: dict = Depends(get_current_user)):
    room = execute_query(
        """
        SELECT status FROM call_rooms 
        WHERE id = %s 
        AND (user_id = %s OR creator_id = %s)
        """,
        (room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )
    if not room:
        return {"status": "ended"}
    return {"status": room["status"]}


@router.get("/history")
def call_history(current_user: dict = Depends(get_current_user)):
    calls = execute_query(
        """
        SELECT cr.*, u.name as creator_name
        FROM call_rooms cr
        JOIN users u ON u.id = cr.creator_id
        WHERE cr.user_id=%s
        ORDER BY cr.id DESC
        LIMIT 20
        """,
        (current_user["id"],),
        fetch_all=True
    )
    return {"success": True, "calls": calls or []}