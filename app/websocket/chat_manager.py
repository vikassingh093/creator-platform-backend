from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.services.jwt_service import verify_token
from app.database import execute_query
from app.redis_client import redis_set, redis_get
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])

# In-memory connections: { user_id: WebSocket }
# For horizontal scaling: replace with Redis pub/sub
active_connections: dict[int, WebSocket] = {}

async def get_or_create_room(user_id: int, creator_user_id: int) -> int:
    """Get or create chat room between user and creator"""
    profile = execute_query(
        "SELECT id FROM creator_profiles WHERE user_id = %s",
        (creator_user_id,),
        fetch_one=True
    )
    if not profile:
        return None

    room = execute_query(
        "SELECT id FROM chat_rooms WHERE user_id = %s AND creator_id = %s",
        (user_id, profile["id"]),
        fetch_one=True
    )
    if room:
        return room["id"]

    room_id = execute_query(
        "INSERT INTO chat_rooms (user_id, creator_id) VALUES (%s, %s)",
        (user_id, profile["id"]),
        last_row_id=True
    )
    return room_id

@router.websocket("/ws/chat/{room_id}")
async def websocket_chat(
    websocket: WebSocket,
    room_id: int,
    token: str = Query(...)
):
    """
    WebSocket endpoint for chat
    Connect: ws://localhost:8000/ws/chat/{room_id}?token=JWT_TOKEN
    """
    # Verify JWT
    payload = verify_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    user_id = int(payload["sub"])

    # Verify user is part of this room
    room = execute_query(
        """
        SELECT cr.id, cr.user_id, cr.creator_id,
               cp.user_id as creator_user_id
        FROM chat_rooms cr
        JOIN creator_profiles cp ON cp.id = cr.creator_id
        WHERE cr.id = %s AND (cr.user_id = %s OR cp.user_id = %s)
        """,
        (room_id, user_id, user_id),
        fetch_one=True
    )

    if not room:
        await websocket.close(code=4003, reason="Room not found or access denied")
        return

    await websocket.accept()
    active_connections[user_id] = websocket
    logger.info(f"User {user_id} connected to room {room_id}")

    # Send previous messages on connect
    messages = execute_query(
        """
        SELECT m.id, m.sender_id, m.message, m.is_read, m.read_at,
               m.created_at, u.name as sender_name
        FROM chat_messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.room_id = %s
        ORDER BY m.created_at ASC
        LIMIT 50
        """,
        (room_id,),
        fetch_all=True
    )

    # Convert datetime to string
    for msg in messages:
        if msg.get("created_at"):
            msg["created_at"] = str(msg["created_at"])
        if msg.get("read_at"):
            msg["read_at"] = str(msg["read_at"])

    await websocket.send_text(json.dumps({
        "type": "history",
        "messages": messages
    }))

    # Mark messages as read
    other_user_id = room["creator_user_id"] if user_id == room["user_id"] else room["user_id"]
    execute_query(
        """
        UPDATE chat_messages
        SET is_read = TRUE, read_at = NOW()
        WHERE room_id = %s AND sender_id = %s AND is_read = FALSE
        """,
        (room_id, other_user_id)
    )

    # Notify other user about read receipt
    if other_user_id in active_connections:
        try:
            await active_connections[other_user_id].send_text(json.dumps({
                "type": "read_receipt",
                "room_id": room_id,
                "read_by": user_id
            }))
        except:
            pass

    try:
        while True:
            data = await websocket.receive_text()
            payload_data = json.loads(data)
            msg_type = payload_data.get("type")

            # Handle typing indicator
            if msg_type == "typing":
                if other_user_id in active_connections:
                    try:
                        await active_connections[other_user_id].send_text(json.dumps({
                            "type": "typing",
                            "user_id": user_id,
                            "is_typing": payload_data.get("is_typing", False)
                        }))
                    except:
                        pass
                continue

            # Handle new message
            if msg_type == "message":
                message_text = payload_data.get("message", "").strip()
                if not message_text:
                    continue

                # Save to DB
                message_id = execute_query(
                    """
                    INSERT INTO chat_messages (room_id, sender_id, message)
                    VALUES (%s, %s, %s)
                    """,
                    (room_id, user_id, message_text),
                    last_row_id=True
                )

                # Get sender info
                sender = execute_query(
                    "SELECT name FROM users WHERE id = %s",
                    (user_id,),
                    fetch_one=True
                )

                message_payload = {
                    "type": "message",
                    "id": message_id,
                    "room_id": room_id,
                    "sender_id": user_id,
                    "sender_name": sender["name"],
                    "message": message_text,
                    "is_read": False,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                # Send to sender (confirmation)
                await websocket.send_text(json.dumps(message_payload))

                # Send to receiver if online
                if other_user_id in active_connections:
                    try:
                        await active_connections[other_user_id].send_text(
                            json.dumps(message_payload)
                        )
                        # Mark as read since receiver is online
                        execute_query(
                            "UPDATE chat_messages SET is_read = TRUE, read_at = NOW() WHERE id = %s",
                            (message_id,)
                        )
                        # Send read receipt back to sender
                        await websocket.send_text(json.dumps({
                            "type": "read_receipt",
                            "message_id": message_id,
                            "room_id": room_id
                        }))
                    except:
                        pass

    except WebSocketDisconnect:
        active_connections.pop(user_id, None)
        logger.info(f"User {user_id} disconnected from room {room_id}")

        # Notify other user offline
        if other_user_id in active_connections:
            try:
                await active_connections[other_user_id].send_text(json.dumps({
                    "type": "user_offline",
                    "user_id": user_id
                }))
            except:
                pass