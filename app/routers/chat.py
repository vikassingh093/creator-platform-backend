from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from app.database import execute_query
from app.middleware.auth_middleware import get_current_user
from app.services.jwt_service import verify_token
from app.services.notification_service import create_notification
from app.utils.helpers import fix_photos, full_image_url

# ✅ Import helpers — all money logic lives there
from app.helpers.wallet_helper import (
    debit_wallet, credit_creator_wallet, get_balance,
    has_sufficient_balance
)
from app.helpers.transaction_helper import (
    record_chat_transaction, calculate_split
)

import logging
import json
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])

active_connections: dict = {}  # {room_id: {user_id: websocket}}
active_billing: dict = {}      # {room_id: billing_task}


@router.post("/start/{creator_id}")
def start_chat(creator_id: int, current_user: dict = Depends(get_current_user)):
    logger.info(f"start_chat: user={current_user['id']} creator_id={creator_id}")

    if current_user["user_type"] == "creator":
        raise HTTPException(status_code=403, detail="Creators cannot start chats")

    creator = execute_query(
        """
        SELECT u.id, u.name, u.profile_photo, cp.chat_rate, cp.is_online
        FROM users u
        JOIN creator_profiles cp ON u.id = cp.user_id
        WHERE u.id = %s AND cp.is_approved = 1 AND cp.is_rejected = 0
        """,
        (creator_id,),
        fetch_one=True
    )
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    if not creator["is_online"]:
        raise HTTPException(status_code=400, detail="Creator is offline")

    # ✅ Use helper to check balance
    balance = get_balance(current_user["id"])
    chat_rate = float(creator["chat_rate"])
    if balance < chat_rate:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Minimum ₹{chat_rate} required"
        )

    existing_room = execute_query(
        "SELECT id, status FROM chat_rooms WHERE user_id = %s AND creator_id = %s ORDER BY id DESC LIMIT 1",
        (current_user["id"], creator_id),
        fetch_one=True
    )

    if existing_room:
        # ✅ Reactivate with billing fields
        execute_query(
            """
            UPDATE chat_rooms 
            SET status = 'active', created_at = CURRENT_TIMESTAMP,
                rate_per_minute = %s, duration = 0, total_cost = 0.00, started_at = NULL
            WHERE id = %s
            """,
            (chat_rate, existing_room["id"])
        )
        logger.info(f"Reactivated room: {existing_room['id']}")

        execute_query(
            "DELETE FROM notifications WHERE reference_id = %s AND type = 'chat'",
            (f"room_{existing_room['id']}",)
        )

        create_notification(
            user_id=creator_id,
            title="New Chat Request 💬",
            message=f"{current_user['name']} wants to chat with you!",
            type="chat",
            reference_id=f"room_{existing_room['id']}"
        )
        create_notification(
            user_id=current_user["id"],
            title="Chat Started 💬",
            message=f"You started a chat with {creator['name']}",
            type="chat",
            reference_id=f"room_{existing_room['id']}"
        )
        return {"success": True, "room_id": existing_room["id"], "creator": creator}

    # ✅ Create new room with rate_per_minute
    execute_query(
        """
        INSERT INTO chat_rooms (user_id, creator_id, status, rate_per_minute)
        VALUES (%s, %s, 'active', %s)
        """,
        (current_user["id"], creator_id, chat_rate)
    )
    room = execute_query(
        "SELECT id FROM chat_rooms WHERE user_id = %s AND creator_id = %s ORDER BY id DESC LIMIT 1",
        (current_user["id"], creator_id),
        fetch_one=True
    )
    logger.info(f"New room created: {room['id']}")

    create_notification(
        user_id=creator_id,
        title="New Chat Request 💬",
        message=f"{current_user['name']} wants to chat with you!",
        type="chat",
        reference_id=f"room_{room['id']}"
    )
    create_notification(
        user_id=current_user["id"],
        title="Chat Started 💬",
        message=f"You started a chat with {creator['name']}",
        type="chat",
        reference_id=f"room_{room['id']}"
    )

    return {"success": True, "room_id": room["id"], "creator": creator}


@router.post("/creator/start-with-customer/{customer_id}")
def creator_start_chat(customer_id: int, current_user: dict = Depends(get_current_user)):
    """Creator initiates a chat with an online customer — FREE for creator to start"""
    logger.info(f"creator_start_chat: creator={current_user['id']} customer_id={customer_id}")

    if current_user["user_type"] != "creator":
        raise HTTPException(status_code=403, detail="Only creators can use this endpoint")

    customer = execute_query(
        "SELECT id, name, profile_photo FROM users WHERE id = %s AND user_type IN ('customer', 'user') AND is_active = 1",
        (customer_id,),
        fetch_one=True
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # ✅ Get creator's chat rate
    creator_profile = execute_query(
        "SELECT chat_rate FROM creator_profiles WHERE user_id = %s",
        (current_user["id"],),
        fetch_one=True
    )
    chat_rate = float(creator_profile["chat_rate"]) if creator_profile else 0

    existing_room = execute_query(
        "SELECT id, status FROM chat_rooms WHERE user_id = %s AND creator_id = %s ORDER BY id DESC LIMIT 1",
        (customer_id, current_user["id"]),
        fetch_one=True
    )

    if existing_room:
        execute_query(
            """
            UPDATE chat_rooms 
            SET status = 'active', created_at = CURRENT_TIMESTAMP,
                rate_per_minute = %s, duration = 0, total_cost = 0.00, started_at = NULL
            WHERE id = %s
            """,
            (chat_rate, existing_room["id"])
        )
        logger.info(f"Reactivated room: {existing_room['id']}")

        execute_query(
            "DELETE FROM notifications WHERE reference_id = %s AND type = 'chat'",
            (f"room_{existing_room['id']}",)
        )

        create_notification(
            user_id=customer_id,
            title="Creator wants to chat! 💬",
            message=f"{current_user['name']} started a chat with you",
            type="chat",
            reference_id=f"room_{existing_room['id']}"
        )

        return {"success": True, "room_id": existing_room["id"], "customer": customer}

    # ✅ Create new room with rate
    execute_query(
        """
        INSERT INTO chat_rooms (user_id, creator_id, status, rate_per_minute)
        VALUES (%s, %s, 'active', %s)
        """,
        (customer_id, current_user["id"], chat_rate)
    )
    room = execute_query(
        "SELECT id FROM chat_rooms WHERE user_id = %s AND creator_id = %s ORDER BY id DESC LIMIT 1",
        (customer_id, current_user["id"]),
        fetch_one=True
    )
    logger.info(f"New room created by creator: {room['id']}")

    create_notification(
        user_id=customer_id,
        title="Creator wants to chat! 💬",
        message=f"{current_user['name']} started a chat with you",
        type="chat",
        reference_id=f"room_{room['id']}"
    )

    return {"success": True, "room_id": room["id"], "customer": customer}


@router.get("/creator/active-rooms")
def get_creator_active_rooms(current_user: dict = Depends(get_current_user)):
    rooms = execute_query(
        """
        SELECT 
            cr.id, cr.user_id, cr.creator_id, cr.status, cr.created_at,
            u.name AS user_name, u.profile_photo AS user_photo
        FROM chat_rooms cr
        JOIN users u ON u.id = cr.user_id
        WHERE cr.creator_id = %s AND cr.status = 'active'
        ORDER BY cr.created_at DESC
        """,
        (current_user["id"],),
        fetch_all=True
    )
    rooms = [fix_photos(r) for r in rooms] if rooms else []
    return {"success": True, "rooms": rooms}


@router.get("/room/{room_id}/messages")
def get_messages(room_id: int, current_user: dict = Depends(get_current_user)):
    room = execute_query(
        "SELECT * FROM chat_rooms WHERE id = %s AND (user_id = %s OR creator_id = %s)",
        (room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    messages = execute_query(
        """
        SELECT * FROM (
            SELECT 
                m.id, m.room_id, m.sender_id, m.message, m.is_read, m.created_at,
                u.name AS sender_name, u.profile_photo AS sender_photo
            FROM chat_messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.room_id = %s
            ORDER BY m.created_at DESC
            LIMIT 50
        ) sub
        ORDER BY created_at ASC
        """,
        (room_id,),
        fetch_all=True
    )
    messages = [fix_photos(m) for m in messages] if messages else []
    return {"success": True, "messages": messages, "room": room}


@router.post("/room/{room_id}/end")
def end_chat(room_id: int, current_user: dict = Depends(get_current_user)):
    room = execute_query(
        "SELECT * FROM chat_rooms WHERE id = %s AND (user_id = %s OR creator_id = %s)",
        (room_id, current_user["id"], current_user["id"]),
        fetch_one=True
    )
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    execute_query("UPDATE chat_rooms SET status = 'ended' WHERE id = %s", (room_id,))

    other_user_id = room["creator_id"] if current_user["id"] == room["user_id"] else room["user_id"]
    create_notification(
        user_id=other_user_id,
        title="Chat Ended 👋",
        message="Chat session has ended",
        type="chat",
        reference_id=f"room_{room_id}"
    )

    return {"success": True, "message": "Chat ended"}


@router.websocket("/ws/{room_id}")
async def websocket_chat(websocket: WebSocket, room_id: int):
    await websocket.accept()

    user = None
    room = None

    try:
        auth_data = await websocket.receive_text()
        auth = json.loads(auth_data)
        token = auth.get("token")

        if not token:
            await websocket.send_text(json.dumps({"type": "error", "message": "No token"}))
            await websocket.close()
            return

        payload = verify_token(token)
        if not payload:
            await websocket.send_text(json.dumps({"type": "error", "message": "Invalid token"}))
            await websocket.close()
            return

        user_id = payload.get("sub") or payload.get("user_id") or payload.get("id")
        user = execute_query(
            "SELECT * FROM users WHERE id = %s",
            (int(user_id),),
            fetch_one=True
        )
        if not user:
            await websocket.send_text(json.dumps({"type": "error", "message": "User not found"}))
            await websocket.close()
            return

        room = execute_query(
            "SELECT * FROM chat_rooms WHERE id = %s AND (user_id = %s OR creator_id = %s) AND status = 'active'",
            (room_id, user["id"], user["id"]),
            fetch_one=True
        )
        if not room:
            await websocket.send_text(json.dumps({"type": "error", "message": "Room not found or ended"}))
            await websocket.close()
            return

        creator = execute_query(
            """
            SELECT cp.chat_rate, u.name 
            FROM creator_profiles cp 
            JOIN users u ON u.id = cp.user_id 
            WHERE cp.user_id = %s
            """,
            (room["creator_id"],),
            fetch_one=True
        )

        # Store connection
        if room_id not in active_connections:
            active_connections[room_id] = {}
        active_connections[room_id][user["id"]] = websocket

        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": "Connected to chat",
            "room_id": room_id,
            "chat_rate": float(creator["chat_rate"]) if creator else 0
        }))

        await broadcast_to_room(room_id, user["id"], {
            "type": "user_joined",
            "user_id": user["id"],
            "name": user["name"]
        })

        # Check if BOTH connected now
        room_connections = active_connections.get(room_id, {})
        both_connected = (
            room["user_id"] in room_connections and
            room["creator_id"] in room_connections
        )

        logger.info(f"Room {room_id} connections: {list(room_connections.keys())} | both_connected: {both_connected}")

        if both_connected:
            rate = float(creator["chat_rate"]) if creator else 0

            # ✅ Mark billing start time on chat_rooms
            execute_query(
                "UPDATE chat_rooms SET started_at = NOW(), rate_per_minute = %s WHERE id = %s AND started_at IS NULL",
                (rate, room_id)
            )

            await broadcast_to_room(room_id, None, {
                "type": "chat_started",
                "message": "Chat started! Billing begins now."
            })

            # Start billing only once per room
            if room_id not in active_billing:
                billing_task = asyncio.create_task(
                    billing_loop(
                        active_connections[room_id].get(room["user_id"]),
                        room_id,
                        room["user_id"],
                        room["creator_id"],
                        rate
                    )
                )
                active_billing[room_id] = billing_task
                logger.info(f"✅ Billing started for room {room_id}")
        else:
            if user["user_type"] == "user":
                await websocket.send_text(json.dumps({
                    "type": "waiting",
                    "message": "Waiting for creator to join..."
                }))
            else:
                await websocket.send_text(json.dumps({
                    "type": "waiting",
                    "message": "Customer is waiting for you!"
                }))

        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)

            if message_data.get("type") == "message":
                msg_text = message_data.get("message", "").strip()
                if not msg_text:
                    continue

                execute_query(
                    "INSERT INTO chat_messages (room_id, sender_id, message, is_read) VALUES (%s, %s, %s, 0)",
                    (room_id, user["id"], msg_text)
                )

                saved_msg = execute_query(
                    "SELECT * FROM chat_messages WHERE room_id = %s AND sender_id = %s ORDER BY id DESC LIMIT 1",
                    (room_id, user["id"]),
                    fetch_one=True
                )

                await broadcast_to_room(room_id, None, {
                    "type": "message",
                    "id": saved_msg["id"] if saved_msg else None,
                    "room_id": room_id,
                    "sender_id": user["id"],
                    "sender_name": user["name"],
                    "sender_photo": full_image_url(user.get("profile_photo")),
                    "message": msg_text,
                    "created_at": str(saved_msg["created_at"]) if saved_msg else None
                })

                other_user_id = room["user_id"] if user["id"] == room["creator_id"] else room["creator_id"]
                other_connected = other_user_id in active_connections.get(room_id, {})

                if not other_connected:
                    create_notification(
                        user_id=other_user_id,
                        title=f"New message from {user['name']} 💬",
                        message=msg_text[:50] + ("..." if len(msg_text) > 50 else ""),
                        type="chat",
                        reference_id=f"room_{room_id}"
                    )
                    logger.info(f"Notification sent to user {other_user_id}")

            elif message_data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: room {room_id} user {user['id'] if user else 'unknown'}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Stop billing when anyone disconnects
        if room_id in active_billing:
            active_billing[room_id].cancel()
            active_billing.pop(room_id, None)
            logger.info(f"⛔ Billing stopped - room {room_id}")

        # ✅ Record summary transaction when chat ends
        if room:
            try:
                final_room = execute_query(
                    "SELECT * FROM chat_rooms WHERE id = %s",
                    (room_id,),
                    fetch_one=True
                )
                total_cost = float(final_room.get("total_cost") or 0) if final_room else 0
                duration = int(final_room.get("duration") or 0) if final_room else 0

                if total_cost > 0 and duration > 0:
                    # Check if summary transaction already exists
                    existing_tx = execute_query(
                        "SELECT id FROM transactions WHERE reference_id = %s AND type = 'chat' LIMIT 1",
                        (f"chat_{room_id}",),
                        fetch_one=True
                    )
                    if not existing_tx:
                        record_chat_transaction(
                            user_id=final_room["user_id"],
                            creator_id=final_room["creator_id"],
                            duration_seconds=duration,
                            total_cost=total_cost,
                            room_id=room_id
                        )
                        logger.info(f"✅ Chat summary transaction: Room {room_id} | ₹{total_cost}")
            except Exception as e:
                logger.error(f"⚠️ Chat summary transaction error: {e}")

        if room and user and room_id in active_connections:
            active_connections[room_id].pop(user["id"], None)
            if not active_connections[room_id]:
                del active_connections[room_id]

        if room and user:
            await broadcast_to_room(room_id, user["id"], {
                "type": "user_left",
                "user_id": user["id"],
                "name": user.get("name", "")
            })


async def broadcast_to_room(room_id: int, exclude_user_id, data: dict):
    if room_id not in active_connections:
        return
    message = json.dumps(data, default=str)
    for uid, ws in list(active_connections[room_id].items()):
        if uid != exclude_user_id:
            try:
                await ws.send_text(message)
            except Exception:
                pass


async def billing_loop(user_websocket, room_id, user_id, creator_id, rate_per_min):
    try:
        logger.info(f"Billing loop started room={room_id} rate=₹{rate_per_min}/min")

        await charge_user(user_websocket, room_id, user_id, creator_id, rate_per_min)

        while True:
            await asyncio.sleep(60)

            room = execute_query(
                "SELECT status FROM chat_rooms WHERE id = %s",
                (room_id,),
                fetch_one=True
            )
            if not room or room["status"] != "active":
                logger.info(f"Room {room_id} ended - stopping billing")
                break

            room_conns = active_connections.get(room_id, {})
            if user_id not in room_conns or creator_id not in room_conns:
                logger.info(f"Someone disconnected room {room_id} - stopping billing")
                break

            await charge_user(user_websocket, room_id, user_id, creator_id, rate_per_min)

    except asyncio.CancelledError:
        logger.info(f"Billing cancelled room {room_id}")
    except Exception as e:
        logger.error(f"Billing error room={room_id}: {e}", exc_info=True)
    finally:
        active_billing.pop(room_id, None)


async def charge_user(user_websocket, room_id: int, user_id: int, creator_id: int, rate_per_min: float):
    """Deduct from user wallet, credit creator wallet with split — using helpers"""

    # ── 1. Check user balance ────────────────────────────────
    balance = get_balance(user_id)

    if balance < rate_per_min:
        execute_query(
            "UPDATE chat_rooms SET status = 'ended' WHERE id = %s",
            (room_id,)
        )
        if user_websocket:
            try:
                await user_websocket.send_text(json.dumps({
                    "type": "chat_ended",
                    "reason": "insufficient_balance",
                    "message": "Chat ended due to insufficient balance"
                }))
            except Exception:
                pass
        logger.info(f"⛔ Chat ended - insufficient balance room={room_id} user={user_id}")
        raise asyncio.CancelledError

    # ── 2. Deduct from user wallet using helper ──────────────
    wallet = debit_wallet(user_id, rate_per_min)
    if wallet is None:
        # Race condition
        execute_query("UPDATE chat_rooms SET status = 'ended' WHERE id = %s", (room_id,))
        raise asyncio.CancelledError

    # ── 3. Calculate split using helper ──────────────────────
    split = calculate_split(rate_per_min)

    # ── 4. Credit creator wallet using helper ────────────────
    credit_creator_wallet(creator_id, split["creator_amount"])

    # ── 5. Update chat_rooms billing fields ──────────────────
    execute_query(
        """
        UPDATE chat_rooms 
        SET duration = COALESCE(duration, 0) + 60,
            total_cost = COALESCE(total_cost, 0) + %s
        WHERE id = %s
        """,
        (rate_per_min, room_id)
    )

    logger.info(
        f"✅ Charged ₹{rate_per_min} room={room_id} | "
        f"creator=₹{split['creator_amount']} commission=₹{split['commission_amount']}"
    )

    # ── 6. Notify user via websocket ─────────────────────────
    new_balance = float(wallet["balance"])

    if user_websocket:
        try:
            await user_websocket.send_text(json.dumps({
                "type": "balance_update",
                "balance": new_balance,
                "deducted": rate_per_min
            }))

            if new_balance < rate_per_min * 2:
                await user_websocket.send_text(json.dumps({
                    "type": "low_balance",
                    "balance": new_balance,
                    "message": "⚠️ Low balance! Please add money to continue"
                }))
        except Exception:
            pass