from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
import logging
import os
import time
import math  # ✅ ADD THIS at top

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

AUDIO_RATE = 20
VIDEO_RATE = 50

class InitiateCallRequest(BaseModel):
    creator_id: int
    call_type: str

class EndCallRequest(BaseModel):
    room_id: int
    duration: int

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

    print(f"📌 Step 1 - Request body: creator_id={creator_id}, call_type={call_type}")
    print(f"📌 Step 1 - Current user: id={current_user['id']}")

    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")
    if call_type not in ["audio", "video"]:
        raise HTTPException(status_code=400, detail="Invalid call type")

    print("📌 Step 2 - Fetching creator from DB...")
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
    print(f"📌 Step 2 - Creator found: {creator is not None} | Name: {creator['name'] if creator else 'N/A'}")

    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    rate = AUDIO_RATE if call_type == "audio" else VIDEO_RATE

    print("📌 Step 3 - Checking wallet balance...")
    wallet = execute_query(
        "SELECT balance FROM wallets WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    balance = float(wallet["balance"]) if wallet else 0
    print(f"📌 Step 3 - Wallet balance: ₹{balance} | Required: ₹{rate}")

    if not wallet or balance < rate:
        raise HTTPException(status_code=400, detail=f"Insufficient balance. Need at least ₹{rate}")

    print("📌 Step 4 - Closing any stuck active calls...")
    # ✅ Close ALL old active calls older than 5 minutes automatically
    execute_query(
        """
        UPDATE call_rooms 
        SET status='ended', ended_at=NOW() 
        WHERE status='active' 
        AND started_at < NOW() - INTERVAL 5 MINUTE
        """
    )
    execute_query(
        "UPDATE call_rooms SET status='ended', ended_at=NOW() WHERE user_id = %s AND status='active'",
        (current_user["id"],)
    )
    print("📌 Step 4 - Stuck calls cleared ✅")

    channel_name = f"call_{current_user['id']}_{creator_id}_{int(time.time())}"
    print(f"📌 Step 5 - Channel name: {channel_name}")

    print("📌 Step 6 - Reading Agora credentials from ENV...")
    app_id = os.getenv("AGORA_APP_ID", "")
    app_certificate = os.getenv("AGORA_APP_CERTIFICATE", "")
    print(f"📌 Step 6 - AGORA_APP_ID: {'✅ Found - ' + app_id[:8] + '...' if app_id else '❌ MISSING'}")
    print(f"📌 Step 6 - AGORA_APP_CERTIFICATE: {'✅ Found - ' + app_certificate[:8] + '...' if app_certificate else '❌ MISSING'}")
    print(f"📌 Step 6 - AGORA_AVAILABLE (package): {AGORA_AVAILABLE}")

    token = None
    print("📌 Step 7 - Generating Agora token...")
    if not AGORA_AVAILABLE:
        print("❌ Step 7 - FAILED: agora_token_builder package not available")
    elif not app_id:
        print("❌ Step 7 - FAILED: AGORA_APP_ID is empty in .env")
    elif not app_certificate:
        print("❌ Step 7 - FAILED: AGORA_APP_CERTIFICATE is empty in .env")
    else:
        try:
            privilege_expired_ts = int(time.time()) + 3600
            print(f"📌 Step 7 - Calling RtcTokenBuilder.buildTokenWithUid...")
            print(f"   app_id={app_id[:8]}... cert={app_certificate[:8]}... channel={channel_name} uid=0 role=1 expiry={privilege_expired_ts}")
            token = RtcTokenBuilder.buildTokenWithUid(
                app_id,
                app_certificate,
                channel_name,
                0,
                1,
                privilege_expired_ts
            )
            print(f"✅ Step 7 - REAL AGORA TOKEN GENERATED!")
            print(f"   Token prefix: {token[:15]}...")
            print(f"   Token length: {len(token)} chars")
            print(f"   Is valid Agora token: {token.startswith('006')}")
        except Exception as e:
            print(f"❌ Step 7 - Token generation FAILED: {type(e).__name__}: {e}")
            logger.error(f"Agora token error: {e}")
            token = None

    print(f"📌 Step 8 - Final token status: {'REAL TOKEN ✅' if token and token.startswith('006') else 'NULL - MOCK MODE ⚠️'}")

    print("📌 Step 9 - Inserting call room into DB...")
    execute_query(
        """
        INSERT INTO call_rooms 
        (user_id, creator_id, call_type, channel_name, status, started_at)
        VALUES (%s, %s, %s, %s, 'active', NOW())
        """,
        (current_user["id"], creator_id, call_type, channel_name)
    )

    room = execute_query(
        "SELECT * FROM call_rooms WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (current_user["id"],),
        fetch_one=True
    )
    print(f"📌 Step 9 - Room created with ID: {room['id']}")

    print("=" * 60)
    print(f"✅ CALL INITIATE COMPLETE - Room {room['id']} | {'REAL AGORA' if token else 'MOCK MODE'}")
    print("=" * 60)

    return {
        "success": True,
        "room_id": room["id"],
        "channel_name": channel_name,
        "token": token,
        "app_id": app_id,
        "call_type": call_type,
        "rate_per_minute": rate,
        "creator": {
            "id": creator["user_id"],
            "name": creator["name"],
        }
    }

@router.post("/end")
def end_call(
    body: EndCallRequest,
    current_user: dict = Depends(get_current_user)
):
    print("=" * 60)
    print(f"📞 END CALL - room_id={body.room_id}, duration={body.duration}")
    print("=" * 60)

    room = execute_query(
        "SELECT * FROM call_rooms WHERE id = %s AND status = 'active'",
        (body.room_id,),
        fetch_one=True
    )

    if not room:
        print("⚠️ Room not found or already ended - returning success")
        return {"success": True, "duration": body.duration, "total_cost": 0}

    # ✅ RULE 1: duration = 0 means creator never picked up = NO CHARGE
    if body.duration == 0:
        print("⚠️ Duration = 0 - creator did NOT pick up - NO CHARGE")
        execute_query(
            "UPDATE call_rooms SET status='ended', ended_at=NOW(), duration=0, total_cost=0 WHERE id=%s",
            (body.room_id,)
        )
        return {"success": True, "duration": 0, "total_cost": 0, "message": "Not answered - no charge"}

    rate = AUDIO_RATE if room["call_type"] == "audio" else VIDEO_RATE

    # ✅ RULE 2: CEIL - 1min 1sec = 2 mins, 2min 1sec = 3 mins
    minutes = math.ceil(body.duration / 60)
    total_cost = minutes * rate
    print(f"📌 Duration: {body.duration}s | Ceil Minutes: {minutes} | Rate: ₹{rate} | Total: ₹{total_cost}")

    # ✅ Fetch USER wallet (not current_user - use room's user_id)
    wallet = execute_query(
        "SELECT balance FROM wallets WHERE user_id = %s",
        (room["user_id"],),
        fetch_one=True
    )
    balance = float(wallet["balance"]) if wallet else 0
    actual_cost = min(total_cost, balance)
    print(f"📌 User balance: ₹{balance} | Actual charge: ₹{actual_cost}")

    # ✅ Deduct user wallet
    execute_query(
        "UPDATE wallets SET balance = balance - %s WHERE user_id = %s",
        (actual_cost, room["user_id"])
    )
    print("✅ User wallet deducted")

    # ✅ Creator gets 80%
    creator_earning = round(actual_cost * 0.8, 2)
    print(f"📌 Creator earning (80%): ₹{creator_earning}")

    try:
        existing = execute_query(
            "SELECT id FROM creator_wallet WHERE creator_id = %s LIMIT 1",
            (room["creator_id"],),
            fetch_one=True
        )
        if existing:
            execute_query(
                "UPDATE creator_wallet SET balance = balance + %s WHERE creator_id = %s",
                (creator_earning, room["creator_id"])
            )
        else:
            execute_query(
                "INSERT INTO creator_wallet (creator_id, balance) VALUES (%s, %s)",
                (room["creator_id"], creator_earning)
            )
        print("✅ Creator wallet updated")
    except Exception as e:
        print(f"⚠️ Creator wallet error: {e}")

    try:
        execute_query(
            """
            INSERT INTO transactions (user_id, type, amount, description, status)
            VALUES (%s, %s, %s, %s, 'success')
            """,
            (
                room["user_id"],
                room["call_type"] + "_call",
                actual_cost,
                f"{room['call_type'].title()} call {minutes} min @ ₹{rate}/min = ₹{actual_cost}"
            )
        )
        print("✅ Transaction recorded")
    except Exception as e:
        print(f"⚠️ Transaction error: {e}")

    execute_query(
        """
        UPDATE call_rooms 
        SET status='ended', ended_at=NOW(), duration=%s, total_cost=%s
        WHERE id=%s
        """,
        (body.duration, actual_cost, body.room_id)
    )
    print("✅ Call room closed")

    print(f"✅ DONE - {body.duration}s = {minutes} mins x ₹{rate} = ₹{actual_cost} charged")

    return {
        "success": True,
        "duration": body.duration,
        "minutes": minutes,
        "total_cost": actual_cost,
        "rate_per_minute": rate
    }

@router.get("/history")
def call_history(current_user: dict = Depends(get_current_user)):
    calls = execute_query(
        """
        SELECT cr.*, u.name as creator_name
        FROM call_rooms cr
        JOIN users u ON u.id = cr.creator_id
        WHERE cr.user_id = %s
        ORDER BY cr.created_at DESC
        LIMIT 20
        """,
        (current_user["id"],),
        fetch_all=True
    )
    return {"success": True, "calls": calls or []}

@router.get("/incoming")
def get_incoming_calls(current_user: dict = Depends(get_current_user)):
    """Creator polls this to get incoming calls - only show calls in last 30 seconds"""
    call = execute_query(
        """
        SELECT cr.*, u.name as caller_name
        FROM call_rooms cr
        JOIN users u ON u.id = cr.user_id
        WHERE cr.creator_id = %s 
        AND cr.status = 'active'
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
    print(f"📞 Incoming call for creator {current_user['id']}: Room {call['id']} from {call['caller_name']}")

    return {
        "call": {
            "room_id": call["id"],
            "channel_name": call["channel_name"],
            "call_type": call["call_type"],
            "caller_name": call["caller_name"],
            "app_id": app_id,
            "token": None  # creator joins with null token
        }
    }