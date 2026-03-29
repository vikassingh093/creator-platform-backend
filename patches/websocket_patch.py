"""
============================================================
🔌 WEBSOCKET PATCH — Apply when polling becomes too heavy
============================================================
HOW TO APPLY:
  1. pip install python-socketio
  2. Copy socket_manager.py to app/
  3. Update main.py (see instructions below)
  4. Update calls.py (see instructions below)

HOW TO REVERT:
  1. Remove socket_manager.py from app/
  2. Undo main.py changes
  3. Undo calls.py changes  
  4. Frontend: set USE_WEBSOCKET=false in .env
============================================================
"""

# ─── FILE 1: app/socket_manager.py (NEW FILE) ───────────

SOCKET_MANAGER_CODE = """
import socketio
import jwt
import os

# Create Socket.IO server
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    logger=False,
    ping_timeout=60,
    ping_interval=25
)

# Map user_id → socket session id
user_sockets = {}  # {user_id: sid}

@sio.event
async def connect(sid, environ, auth):
    '''
    Client connects with JWT token
    We verify token and map user_id → sid
    '''
    try:
        token = auth.get('token') if auth else None
        if not token:
            print(f"❌ Socket rejected: no token")
            return False  # reject connection

        secret = os.getenv("JWT_SECRET", "your-secret-key")
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        user_id = payload.get("user_id")

        if not user_id:
            return False

        user_sockets[int(user_id)] = sid
        print(f"🔌 Socket connected: user {user_id} → {sid}")
        return True

    except Exception as e:
        print(f"❌ Socket auth failed: {e}")
        return False

@sio.event
async def disconnect(sid):
    '''Remove user from socket map on disconnect'''
    for uid, s in list(user_sockets.items()):
        if s == sid:
            del user_sockets[uid]
            print(f"🔌 Socket disconnected: user {uid}")
            break

async def notify_incoming_call(user_id: int, call_data: dict):
    '''
    Push incoming call to specific user
    Called from calls.py when creator/customer initiates a call
    '''
    sid = user_sockets.get(user_id)
    if sid:
        await sio.emit('incoming_call', call_data, to=sid)
        print(f"📞 Pushed incoming call to user {user_id}")
        return True
    else:
        print(f"⚠️ User {user_id} not connected via WebSocket")
        return False

async def notify_call_accepted(user_id: int, room_id: int):
    '''Notify caller that their call was accepted'''
    sid = user_sockets.get(user_id)
    if sid:
        await sio.emit('call_accepted', {'room_id': room_id}, to=sid)

async def notify_call_rejected(user_id: int, room_id: int):
    '''Notify caller that their call was rejected'''
    sid = user_sockets.get(user_id)
    if sid:
        await sio.emit('call_rejected', {'room_id': room_id}, to=sid)

async def notify_call_ended(user_id: int, room_id: int):
    '''Notify other party that call ended'''
    sid = user_sockets.get(user_id)
    if sid:
        await sio.emit('call_ended', {'room_id': room_id}, to=sid)

def is_user_online(user_id: int) -> bool:
    '''Check if user has active WebSocket connection'''
    return int(user_id) in user_sockets
"""


# ─── FILE 2: Changes to app/main.py ─────────────────────

MAIN_PY_CHANGES = """
# ADD these imports at top:
import socketio as socketio_lib
from app.socket_manager import sio

# ADD after app = FastAPI(...):
socket_app = socketio_lib.ASGIApp(
    sio, 
    other_app=app,
    socketio_path='/ws/socket.io'
)

# CHANGE uvicorn.run at bottom:
# FROM: uvicorn.run(app, ...)
# TO:   uvicorn.run(socket_app, ...)
"""


# ─── FILE 3: Changes to app/routers/calls.py ────────────

CALLS_PY_CHANGES = """
# ADD import at top:
from app.socket_manager import (
    notify_incoming_call, 
    notify_call_accepted,
    notify_call_rejected, 
    notify_call_ended
)

# IN initiate_call() — after creating room, ADD:
    await notify_incoming_call(creator_id, {
        'room_id': room_id,
        'channel_name': channel_name,
        'call_type': call_type,
        'caller_name': current_user.get('name', 'User'),
        'token': creator_token,
        'uid': 2
    })

# IN creator_initiate_call() — after creating room, ADD:
    await notify_incoming_call(customer_id, {
        'room_id': room_id,
        'channel_name': channel_name,
        'call_type': call_type,
        'caller_name': current_user.get('name', 'Creator'),
        'token': customer_token,
        'uid': 2,
        'initiated_by': 'creator'
    })

# IN accept_call() — ADD:
    await notify_call_accepted(caller_user_id, room_id)

# IN reject_call() — ADD:
    await notify_call_rejected(caller_user_id, room_id)

# IN end_call() — ADD:
    other_user_id = room['creator_id'] if current_user['id'] == room['user_id'] else room['user_id']
    await notify_call_ended(other_user_id, room_id)
"""