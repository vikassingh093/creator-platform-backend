from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import APP_NAME
from app.routers import auth, users, creators, chat, wallet, content, admin, notifications, calls, admin_offers, offers
# from app.routers import webhook  # TODO: Enable after Razorpay dashboard config
from app.middleware.activity_tracker import ActivityTrackerMiddleware
import os

app = FastAPI(title=APP_NAME)

# ✅ Activity tracker FIRST (so it runs AFTER CORS in the middleware chain)
app.add_middleware(ActivityTrackerMiddleware)

# ✅ CORS LAST (so it runs FIRST — handles OPTIONS before anything else)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Serve uploaded files as static
os.makedirs("uploads/images", exist_ok=True)
os.makedirs("uploads/videos", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ✅ Include routers (keep your existing includes below)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(creators.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(wallet.router, prefix="/api/v1")
app.include_router(content.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(calls.router, prefix="/api/v1")
app.include_router(admin_offers.router, prefix="/api/v1")
app.include_router(offers.router, prefix="/api/v1")
# app.include_router(webhook.router)  # TODO: Enable after Razorpay dashboard config